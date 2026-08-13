#!/usr/bin/env python3
"""
Picamera.py - YOLO11n + IMX500 推論スクリプト
"""
import numpy as np
import cv2
import time
import os
import sys
import shutil
import threading
import queue
import csv
import json
import math
import socket
from functools import lru_cache
from collections import deque
from datetime import datetime

try:
    import spidev as _spidev
    import gpiod as _gpiod
    _LCD_HW_OK = True
except ImportError:
    _LCD_HW_OK = False

from picamera2 import Picamera2, Preview  # type: ignore
from picamera2.devices import IMX500  # type: ignore
from picamera2.devices.imx500 import NetworkIntrinsics, postprocess_nanodet_detection  # type: ignore

# --- 設定 ---
MODEL_CONFIG_PATH = "/home/pi/model_config.json"  # 永続的な場所に保存（再起動後も保持）
MODEL_STATUS_PATH = "/tmp/model_status.json"

# 起動直後にステータスを「起動中」に設定
with open(MODEL_STATUS_PATH, 'w') as f:
    json.dump({'status': 'starting', 'model_type': '', 'timestamp': time.time()}, f)

SAVE_CONFIG_PATH = "/home/pi/save_config.json"  # 保存設定を永続化
THRESHOLD_CONFIG_PATH = "/home/pi/threshold_config.json"  # しきい値設定を永続化
RECOGNITION_CONFIG_PATH = "/home/pi/recognition_config.json"  # 認識設定を永続化
DEFAULT_MODEL_PATH = "/home/pi/object_detection/network.rpk"
DEFAULT_LABELS_PATH = "/home/pi/object_detection/labels.txt"
TEMP_IMAGE_PATH = "/tmp/result_tmp.jpg"
WEB_IMAGE_PATH = "/tmp/result.jpg"
LOG_DIR = "/home/pi/logs"
RAW_IMAGE_DIR = "/home/pi/raw_images"
IMAGE_DIR = "/home/pi/images"
SEND_LOG_DIR = "/home/pi/send_logs"  # 送信用ログ（1日ごとローテーション）
STATUS_CODE_CONFIG_PATH = "/home/pi/status_code_config.json"  # 状態コードのパターン定義
ID_NAMES_CONFIG_PATH = "/home/pi/id_names_config.json"  # ID名前マッピング
IOU_THRESHOLD = 0.3  # NMS IoU閾値
MAX_DETECTIONS = 99
SAVE_INTERVAL = 1
# 保存画像の枚数上限（古い順に自動削除）。約1枚/秒で保存されるため、
# 3000枚 ≒ 50分。ディレクトリ肥大によるSD I/O逼迫・空き容量枯渇を防ぐ。
# 保持時間を増減したい場合はこの値を変更する。
MAX_RAW_IMAGES = 3000
MAX_RESULT_IMAGES = 3000
FILECAP_INTERVAL = 60.0   # 枚数上限チェックの実行間隔（秒）
# 1回あたりの削除上限。初回に数千枚を一気に os.remove するとSDが飽和し
# web_serverワーカーをI/O待ちで巻き込むため、段階的に間引く。
# 定常時は約60枚/分の増加なのでこの値で十分追従できる。
FILECAP_MAX_DELETE_PER_RUN = 300
INPUT_SIZE = 640
SIGNAL_COLOR_PREFIXES = ['red', 'yellow', 'green', 'blue', 'white', 'off']  # 信号色プレフィックス（定数）
ID_REGION_COLOR = (255, 0, 170, 255)  # シグナルタワーID枠の描画色（紫, BGRA）

# しきい値は動的に変更可能（初期値）
THRESHOLD = 0.25  # 検出閾値

# モデル設定を読み込む
def load_model_config():
    """モデル設定をファイルから読み込み"""
    if os.path.exists(MODEL_CONFIG_PATH):
        try:
            with open(MODEL_CONFIG_PATH, 'r') as f:
                config = json.load(f)
                return config.get('network', DEFAULT_MODEL_PATH), config.get('labels', DEFAULT_LABELS_PATH)
        except:
            pass
    return DEFAULT_MODEL_PATH, DEFAULT_LABELS_PATH

MODEL_PATH, LABELS_PATH = load_model_config()
print(f"設定ファイルから読み込み: MODEL={MODEL_PATH}, LABELS={LABELS_PATH}")

# モデルタイプを判定
if 'signal' in MODEL_PATH.lower():
    MODEL_TYPE = 'signal_tower'
else:
    MODEL_TYPE = 'object_detection'

def check_model_changed():
    """モデル設定が変更されたかチェック"""
    new_model, new_labels = load_model_config()
    return new_model != MODEL_PATH or new_labels != LABELS_PATH


def load_recognition_config():
    """認識設定をファイルから読み込み"""
    if os.path.exists(RECOGNITION_CONFIG_PATH):
        try:
            with open(RECOGNITION_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'enabled': True, 'resolution': '640x640'}


def load_save_config():
    """保存設定をファイルから読み込み"""
    if os.path.exists(SAVE_CONFIG_PATH):
        try:
            with open(SAVE_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    # デフォルトは両方ON
    return {'save_result': True, 'save_raw': True}


def load_threshold_config():
    """しきい値設定をファイルから読み込み"""
    if os.path.exists(THRESHOLD_CONFIG_PATH):
        try:
            with open(THRESHOLD_CONFIG_PATH, 'r') as f:
                config = json.load(f)
                return config.get('threshold', 0.25)
        except:
            pass
    # デフォルトは0.25
    return 0.25


def set_model_status_ready():
    """モデルステータスを「準備完了」に設定"""
    status_data = {
        'status': 'ready',
        'model_type': MODEL_TYPE,
        'timestamp': time.time()
    }
    with open(MODEL_STATUS_PATH, 'w') as f:
        json.dump(status_data, f)
    print(f"モデルステータス: ready ({MODEL_TYPE})")


class Detection:
    """検出結果を格納するクラス"""
    def __init__(self, coords, category, conf, metadata):
        self.category = category
        self.conf = conf
        # set_inference_roi_absでROIを制限した場合、推論座標はROI相対(0-1)になる。
        # convert_inference_coordsはフルセンサー(4056x3040)基準を前提とするため、
        # ROI相対座標をフルセンサー正規化座標に変換してから渡す。
        # 座標軸の対応: y0,y1 → センサーY方向(3040) / x0,x1 → センサーX方向(4056)
        if current_inference_roi is not None:
            roi_x, roi_y, roi_w, roi_h = current_inference_roi
            y0, x0, y1, x1 = coords
            coords = (
                (roi_y + y0 * roi_h) / 3040,  # ROI Y補正 → フルセンサーY正規化
                (roi_x + x0 * roi_w) / 4056,  # ROI X補正 → フルセンサーX正規化
                (roi_y + y1 * roi_h) / 3040,
                (roi_x + x1 * roi_w) / 4056,
            )
        self.coords = coords  # フルセンサー正規化座標
        self.box = imx500.convert_inference_coords(coords, metadata, picam2)


def parse_detections(metadata: dict):
    """出力テンソルをパースして検出オブジェクトを返す"""
    global last_detections, last_np_outputs_none_count, THRESHOLD
    global inference_ok_count, inference_none_count, inference_history, inference_ok_in_window
    global frame_count

    np_outputs = imx500.get_outputs(metadata, add_batch=True)
    input_w, input_h = imx500.get_input_size()

    if np_outputs is None:
        last_np_outputs_none_count += 1
        inference_none_count += 1
        if len(inference_history) == inference_history.maxlen and inference_history[0]:
            inference_ok_in_window -= 1
        inference_history.append(False)
        # IMX500が推論していない場合は空のリストを返す（古い結果を使わない）
        return []

    # 正常に出力を取得
    last_np_outputs_none_count = 0
    inference_ok_count += 1
    if len(inference_history) == inference_history.maxlen and inference_history[0]:
        inference_ok_in_window -= 1
    inference_ok_in_window += 1
    inference_history.append(True)

    # 後処理
    if intrinsics.postprocess == "nanodet":
        # nanodet形式の後処理
        boxes, scores, classes = postprocess_nanodet_detection(
            outputs=np_outputs[0],
            conf=THRESHOLD,
            iou_thres=IOU_THRESHOLD,
            max_out_dets=MAX_DETECTIONS
        )[0]
        from picamera2.devices.imx500.postprocess import scale_boxes  # type: ignore
        boxes = scale_boxes(boxes, 1, 1, input_h, input_w, False, False)
    else:
        # YOLO11n MultiClassNMS後処理済み形式
        # 出力: [boxes, scores, classes, num_detections]
        boxes = np_outputs[0][0]       # (300, 4) - [ymin, xmin, ymax, xmax]
        scores = np_outputs[1][0]      # (300,)
        classes = np_outputs[2][0]     # (300,)

        # num_detectionsを確認
        if len(np_outputs) > 3:
            num_dets = int(np_outputs[3][0][0]) if np_outputs[3].size > 0 else 0
            # 有効な検出のみ使用
            if num_dets > 0:
                boxes = boxes[:num_dets]
                scores = scores[:num_dets]
                classes = classes[:num_dets]

        # ボックス座標の正規化チェック
        if np.max(boxes) > 10:  # ピクセル座標の場合
            # 640x640モデル入力サイズで正規化
            boxes = boxes / 640.0

        if intrinsics.bbox_normalization:
            boxes = boxes / input_h

        # YOLO形式は [ymin, xmin, ymax, xmax] → [xmin, ymin, xmax, ymax] に変換
        if boxes.shape[1] == 4:
            boxes = boxes[:, [1, 0, 3, 2]]

        # convert_inference_coords用にリスト形式に変換
        boxes_list = [box.tolist() for box in boxes]

    # 検出結果をフィルタリング
    last_detections = []
    for box, score, category in zip(boxes_list, scores, classes):
        if score > THRESHOLD:
            try:
                det = Detection(box, category, score, metadata)
                last_detections.append(det)
            except Exception:
                continue

    return last_detections


@lru_cache
def get_labels():
    """ラベルリストを取得"""
    labels = intrinsics.labels
    if intrinsics.ignore_dash_labels:
        labels = [label for label in labels if label and label != "-"]
    return labels


def apply_nms_to_detections(detections, iou_threshold=0.5):
    """検出結果にNMSを適用して重複を除去"""
    if len(detections) == 0:
        return []

    # ボックス座標とスコアを抽出
    boxes = []
    scores = []
    for det in detections:
        x, y, w, h = det.box
        boxes.append([x, y, x + w, y + h])
        scores.append(det.conf)

    boxes = np.array(boxes)
    scores = np.array(scores)

    # NMSを適用
    indices = cv2.dnn.NMSBoxes(
        boxes.tolist(),
        scores.tolist(),
        score_threshold=0.0,
        nms_threshold=iou_threshold
    )

    # インデックスをフラット化
    if len(indices) > 0:
        indices = indices.flatten()
        return [detections[i] for i in indices]
    return []


def get_color_bgr(color_name):
    """色名からBGR値を取得"""
    color_map = {
        'RED': (0, 0, 255),
        'YELLOW': (0, 255, 255),
        'GREEN': (0, 255, 0),
        'BLUE': (255, 0, 0),
        'WHITE': (255, 255, 255)
    }
    return color_map.get(color_name, (0, 255, 0))


def get_label_color(label):
    """物体名から連想される色をBGRで返す"""
    label_lower = label.lower()

    # 物体名の先頭に色名が含まれる場合（シグナルタワー2モデル用）
    if label_lower.startswith('red'):
        return (0, 0, 255)  # 赤
    if label_lower.startswith('yellow'):
        return (0, 255, 255)  # 黄
    if label_lower.startswith('green'):
        return (0, 255, 0)  # 緑
    if label_lower.startswith('blue'):
        return (255, 0, 0)  # 青
    if label_lower.startswith('white'):
        return (255, 255, 255)  # 白
    if label_lower.startswith('off'):
        return (50, 50, 50)  # 黒（暗いグレー）

    # 人物系 - 肌色/オレンジ
    if label_lower in ['person', 'people', 'man', 'woman', 'child', 'baby']:
        return (0, 165, 255)  # オレンジ

    # 動物系
    if label_lower in ['cat', 'dog', 'bird', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe']:
        return (0, 200, 255)  # 黄色系

    # 乗り物系 - 青
    if label_lower in ['car', 'truck', 'bus', 'train', 'motorcycle', 'bicycle', 'airplane', 'boat', 'ship']:
        return (255, 100, 0)  # 青

    # 食べ物系 - 緑/赤
    if label_lower in ['apple', 'orange', 'banana', 'broccoli', 'carrot', 'pizza', 'donut', 'cake', 'sandwich', 'hot dog']:
        return (0, 200, 0)  # 緑

    # 電子機器系 - シアン
    if label_lower in ['tv', 'laptop', 'mouse', 'keyboard', 'cell phone', 'remote', 'microwave', 'oven', 'toaster', 'refrigerator']:
        return (255, 255, 0)  # シアン

    # 家具系 - 茶色
    if label_lower in ['chair', 'couch', 'bed', 'dining table', 'desk', 'bench']:
        return (50, 100, 150)  # 茶色

    # スポーツ用品系 - マゼンタ
    if label_lower in ['sports ball', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket', 'frisbee', 'skis', 'snowboard', 'kite']:
        return (255, 0, 255)  # マゼンタ

    # 容器系 - 水色
    if label_lower in ['bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'vase', 'potted plant']:
        return (255, 200, 100)  # 水色

    # 身につけるもの - ピンク
    if label_lower in ['backpack', 'umbrella', 'handbag', 'tie', 'suitcase']:
        return (200, 100, 255)  # ピンク

    # 本・時計系 - 白
    if label_lower in ['book', 'clock', 'scissors', 'teddy bear', 'hair drier', 'toothbrush']:
        return (255, 255, 255)  # 白

    # 交通標識系 - 赤
    if label_lower in ['stop sign', 'parking meter', 'traffic light', 'fire hydrant']:
        return (0, 0, 255)  # 赤

    # シグナルタワー系 - シアン
    if 'signal' in label_lower or 'tower' in label_lower:
        return (255, 255, 0)  # シアン

    # デフォルト - ライム緑
    return (0, 255, 0)


# 認識OFFモード: 解像度再設定中はSaveWorkerのcapture_arrayを一時停止するためのイベント
camera_reconfig_event = threading.Event()


class SaveWorker(threading.Thread):
    def __init__(self, save_queue, config_cache):
        super().__init__()
        self.save_queue = save_queue
        self.config_cache = config_cache
        self.daemon = True
        self.running = True
        self.last_capture_time = 0.0  # 認識OFFモード: 1秒ごとのキャプチャ管理
        self.last_save_time = 0.0
        self.last_raw_save_time = 0.0
        self.last_log_time = 0.0
        self.last_cleanup_time = 0.0
        self.last_filecap_time = 0.0

    def _poll_config(self):
        """設定ファイル・システム情報をバックグラウンドで取得しキャッシュを更新する。
        メインスレッドのファイルI/O・syscallをオフロードしてV4L2タイムアウトを防止する。"""
        try:
            rec = load_recognition_config()
            self.config_cache['recognition_enabled'] = rec.get('enabled', True)
            self.config_cache['resolution'] = rec.get('resolution', '640x640')
            self.config_cache['camera_resolution'] = rec.get('camera_resolution', CAMERA_RESOLUTION)
            if RECOGNITION_ENABLED:
                self.config_cache['model_changed'] = check_model_changed()
                self.config_cache['threshold'] = load_threshold_config()
            self.config_cache['save_config'] = load_save_config()
            # ディスク空き容量（statvfs syscall → SDカードで数msブロックすることがある）
            free_gb = check_disk_space("/home/pi")
            if free_gb < 1.0:
                now_cl = time.time()
                # 60秒に1回クリーンアップを実行（連続削除を防止）
                if now_cl - self.last_cleanup_time >= 60.0:
                    print(f"[WARNING] ディスク空き容量不足: {free_gb:.2f}GB 残り（古いファイルを削除します）")
                    cleanup_old_files(target_free_gb=1.0)
                    self.last_cleanup_time = now_cl
                    free_gb = check_disk_space("/home/pi")
                else:
                    print(f"[WARNING] ディスク空き容量不足: {free_gb:.2f}GB 残り（1GB未満のため画像保存を停止）")
            self.config_cache['disk_space_ok'] = free_gb >= 1.0
            # 保存画像の枚数上限を一定間隔で適用（ディレクトリ肥大を防止）。
            # 空き容量に依存する cleanup_old_files と独立に、常に上限を維持する。
            now_fc = time.time()
            if now_fc - self.last_filecap_time >= FILECAP_INTERVAL:
                self.last_filecap_time = now_fc
                enforce_file_cap(RAW_IMAGE_DIR, "raw_", MAX_RAW_IMAGES)
                enforce_file_cap(IMAGE_DIR, "result_", MAX_RESULT_IMAGES)
            # CPU温度（sysfsファイル読み込み）
            self.config_cache['cpu_temp'] = get_cpu_temp()
        except Exception as e:
            print(f"[CONFIG] 設定ポーリングエラー: {e}")

    def run(self):
        last_cfg_poll = 0.0
        last_res_poll = 0.0
        while self.running:
            try:
                task = self.save_queue.get(timeout=0.1)
                frame_copy, ann_snap, label_list, st_log_data, object_log_data = task
                now_sw = time.time()
                save_cfg = self.config_cache.get('save_config', {})
                disk_ok = self.config_cache.get('disk_space_ok', True)
                # 生画像保存判定（アノテーション合成前に保存）
                if disk_ok and save_cfg.get('save_raw', True) and now_sw - self.last_raw_save_time >= 1.0:
                    self.last_raw_save_time = now_sw
                    raw_filename = datetime.now().strftime("raw_%Y%m%d_%H%M%S.jpg")
                    raw_path = os.path.join(RAW_IMAGE_DIR, raw_filename)
                    cv2.imwrite(raw_path, frame_copy, [cv2.IMWRITE_JPEG_QUALITY, 80])
                # Web画像更新（FHDはann_snapをframe_copyサイズにアップスケール後合成）
                frame_h, frame_w = frame_copy.shape[:2]
                if ann_snap.shape[1] != frame_w or ann_snap.shape[0] != frame_h:
                    ann_snap = cv2.resize(ann_snap, (frame_w, frame_h), interpolation=cv2.INTER_NEAREST)
                ann_mask = ann_snap[:, :, 3] > 0
                frame_copy[ann_mask] = ann_snap[ann_mask, :3]
                cv2.imwrite(TEMP_IMAGE_PATH, frame_copy, [cv2.IMWRITE_JPEG_QUALITY, 80])
                os.replace(TEMP_IMAGE_PATH, WEB_IMAGE_PATH)
                # 永久保存判定
                if disk_ok and save_cfg.get('save_result', True) and now_sw - self.last_save_time > SAVE_INTERVAL:
                    self.last_save_time = now_sw
                    labels_str = "_".join(label_list[:3]).replace(" ", "_").replace("/", "-")
                    if len(labels_str) > 50:
                        labels_str = labels_str[:50]
                    if not labels_str:
                        labels_str = "no_detection"
                    timestamp = time.strftime("%Y%m%d%H%M%S")
                    save_dest = f"/home/pi/images/result_{timestamp}_{labels_str}.jpg"
                    shutil.copyfile(WEB_IMAGE_PATH, save_dest)
                # CSVログ書き込み判定
                if now_sw - self.last_log_time >= 1.0:
                    self.last_log_time = now_sw
                    if st_log_data:
                        log_signal_tower(st_log_data)
                    if object_log_data:
                        log_object_detection(object_log_data)
                self.save_queue.task_done()
            except queue.Empty:
                if not RECOGNITION_ENABLED:
                    # 認識OFFモード: 1秒ごとにpicam2から直接画像を取得して保存
                    now_cap = time.time()
                    if now_cap - self.last_capture_time >= 1.0 and not camera_reconfig_event.is_set():
                        try:
                            frame = picam2.capture_array("main")
                            save_cfg = self.config_cache.get('save_config', {})
                            disk_ok = self.config_cache.get('disk_space_ok', True)
                            cpu_temp = self.config_cache.get('cpu_temp', 0.0)
                            # ステータス情報を描画（認識ONと同形式）
                            h, w = frame.shape[:2]
                            res_setting = self.config_cache.get('resolution', 'full')
                            cv2.putText(frame, f"FPS: {fps}", (5, TXT_Y1),
                                        cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, (255, 255, 255), LINE_THICK)
                            cv2.putText(frame, "REC: OFF", (5, TXT_Y2),
                                        cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, (128, 128, 128), LINE_THICK)
                            # CPU温度を右上に表示
                            temp_text = f"{cpu_temp:.1f}C"
                            temp_color = (0, 255, 0) if cpu_temp < 70 else (0, 255, 255) if cpu_temp < 80 else (0, 0, 255)
                            (tw, _), _ = cv2.getTextSize(temp_text, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
                            cv2.putText(frame, temp_text, (w - tw - 5, TXT_Y1),
                                        cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, temp_color, LINE_THICK)
                            # クロップ設定を左下に表示
                            try:
                                _val = int(res_setting)
                                crop_text = '100%' if _val >= 100 else f'{_val}%'
                            except (ValueError, TypeError):
                                crop_text = '100%'
                            cv2.putText(frame, crop_text, (5, h - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, (0, 255, 255), LINE_THICK)
                            # 日時を右下に表示
                            dt_text = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
                            (dtw, _), _ = cv2.getTextSize(dt_text, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
                            cv2.putText(frame, dt_text, (w - dtw - 5, h - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, (255, 255, 255), LINE_THICK)
                            cv2.imwrite(TEMP_IMAGE_PATH, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                            os.replace(TEMP_IMAGE_PATH, WEB_IMAGE_PATH)
                            if disk_ok and save_cfg.get('save_raw', True):
                                raw_filename = datetime.now().strftime("raw_%Y%m%d_%H%M%S.jpg")
                                raw_path = os.path.join(RAW_IMAGE_DIR, raw_filename)
                                cv2.imwrite(raw_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        except Exception as cap_e:
                            print(f"[CAPTURE] 認識OFFモード画像取得エラー: {cap_e}")
                        self.last_capture_time = now_cap
            except Exception as e:
                print(f"Save worker error: {e}")
                try:
                    self.save_queue.task_done()
                except ValueError:
                    pass
            now_poll = time.time()
            if now_poll - last_cfg_poll >= 1.0:
                self._poll_config()
                last_cfg_poll = now_poll
                last_res_poll = now_poll
            elif now_poll - last_res_poll >= 0.1:
                # 解像度だけ高頻度で更新（ファイルI/Oは小さいJSONのみ）
                try:
                    rec = load_recognition_config()
                    self.config_cache['resolution'] = rec.get('resolution', 'full')
                except Exception:
                    pass
                last_res_poll = now_poll


_ip_cache = {'ip': '', 'ts': 0.0}

def _get_ip_cached():
    """IPアドレスを取得（30秒キャッシュ）"""
    now = time.time()
    if now - _ip_cache['ts'] > 30.0:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            _ip_cache['ip'] = s.getsockname()[0]
            s.close()
        except Exception:
            _ip_cache['ip'] = '?.?.?.?'
        _ip_cache['ts'] = now
    return _ip_cache['ip']


def get_cpu_temp():
    """CPU温度を取得（℃）"""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return int(f.read().strip()) / 1000.0
    except:
        return 0.0


def check_disk_space(path="/home/pi"):
    """ディスクの空き容量をチェック（GB単位で返す）"""
    try:
        stat = shutil.disk_usage(path)
        free_gb = stat.free / (1024 ** 3)  # バイトをGBに変換
        return free_gb
    except:
        return 999  # エラー時は十分な容量があると仮定


def enforce_file_cap(directory, prefix, max_count):
    """directory内の prefix で始まる .jpg を max_count 枚に制限し、超過分を
    古い順に削除する。ファイル名先頭が時刻文字列(raw_YYYYmmdd_..., result_YYYYmmdd...)
    のため、stat呼び出しを大量に行わずファイル名のソートだけで時系列順に削除でき、
    巨大ディレクトリでもSD I/O負荷を最小化できる。"""
    try:
        names = [n for n in os.listdir(directory)
                 if n.startswith(prefix) and n.endswith('.jpg')]
    except FileNotFoundError:
        return 0
    except Exception as e:
        print(f"[FILECAP] 一覧取得エラー {directory}: {e}")
        return 0
    if len(names) <= max_count:
        return 0
    names.sort()  # ファイル名先頭のタイムスタンプで時系列順（古い順）
    over = len(names) - max_count
    # 1回の削除数を制限し、SD飽和による連鎖フリーズを防ぐ（残りは次回以降）
    to_remove = names[:min(over, FILECAP_MAX_DELETE_PER_RUN)]
    deleted = 0
    for n in to_remove:
        try:
            os.remove(os.path.join(directory, n))
            deleted += 1
        except Exception:
            pass
    if deleted:
        remaining = over - deleted
        print(f"[FILECAP] {directory}: {deleted}件削除（上限{max_count}枚, 残り超過{remaining}件）")
    return deleted


def cleanup_old_files(target_free_gb=1.0):
    """空き容量が target_free_gb を下回っている間、古いファイルから順に削除する。
    削除対象: /home/pi/images, /home/pi/raw_images, /home/pi/logs（更新日時の古い順）"""
    CLEAN_DIRS = [
        '/home/pi/images',
        '/home/pi/raw_images',
        '/home/pi/logs',
    ]
    candidates = []
    for d in CLEAN_DIRS:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            fpath = os.path.join(d, fname)
            if os.path.isfile(fpath):
                try:
                    candidates.append((os.path.getmtime(fpath), fpath))
                except Exception:
                    pass
    candidates.sort()  # 古い順

    deleted = 0
    for _, fpath in candidates:
        if check_disk_space("/home/pi") >= target_free_gb:
            break
        try:
            os.remove(fpath)
            deleted += 1
            print(f"[CLEANUP] 削除: {fpath}")
        except Exception as e:
            print(f"[CLEANUP] 削除失敗: {fpath}: {e}")

    if deleted:
        print(f"[CLEANUP] {deleted}件削除、空き容量: {check_disk_space('/home/pi'):.2f}GB")
    return deleted


def get_log_file_path():
    """モデルタイプに応じたログファイルパスを返す"""
    date_str = datetime.now().strftime("%Y%m%d")
    return os.path.join(LOG_DIR, f"log_{MODEL_TYPE}_{date_str}.csv")


# シグナルタワー用：色の出現履歴（点灯/点滅判定用）
st_color_history = {}  # key: (grid_x, grid_y, color) -> deque of bool (検出されたかどうか)
st_blink_start_time = {}  # key: (grid_x, grid_y, color) -> 点滅開始タイムスタンプ（3秒継続判定用）
st_last_detected_time = {}  # key: (grid_x, grid_y, color) -> 最後に検出された時刻（再点灯時リセット用）

# 変化ログ用状態追跡
st_change_prev_state = {}    # key: (region_id, color) → 'on'|'blink'|'off'
st_change_last_seen = {}     # key: (region_id, color) → 最後に検出されたタイムスタンプ
obj_change_logged_state = {} # key: label → 'detected'|'lost'
obj_change_last_seen = {}    # key: label → 最後に検出されたタイムスタンプ
ST_OFF_DEBOUNCE = 2.0        # 消灯判定デバウンス秒数
OBJ_LOST_DEBOUNCE = 2.0      # 消失判定デバウンス秒数

# シグナルタワーID領域設定
SIGNAL_ID_TRIGGER_PATH = '/tmp/set_signal_ids'
SIGNAL_ID_RESET_PATH = '/tmp/reset_signal_ids'
SIGNAL_REGIONS_PATH = '/home/pi/signal_regions.json'
signal_regions = []  # [{'id':1,'cx':x,'cy':y,'radius':r}, ...]

def load_signal_regions():
    try:
        if os.path.exists(SIGNAL_REGIONS_PATH):
            with open(SIGNAL_REGIONS_PATH, 'r') as f:
                return json.load(f)
    except:
        pass
    return []

def save_signal_regions_file(regions):
    try:
        with open(SIGNAL_REGIONS_PATH, 'w') as f:
            json.dump(regions, f)
    except Exception as e:
        print(f"[SIGNAL] 領域保存エラー: {e}")

def find_signal_region_id(cx, cy):
    for region in signal_regions:
        if 'x_half_width' in region:
            if abs(cx - region['cx']) <= region['x_half_width']:
                if region.get('y_top', 0) <= cy <= region.get('y_bottom', 99999):
                    return region['id']
        else:
            dist = math.sqrt((cx - region['cx'])**2 + (cy - region['cy'])**2)
            if dist <= region['radius']:
                return region['id']
    return 0

signal_regions = load_signal_regions()
_id_collecting = False
_id_collect_start = 0.0
_id_collect_centers = []


def log_object_detection(detections_data):
    """物体認識モデル: 1秒間に検出された全物体名と座標を記録"""
    log_file = get_log_file_path()
    file_exists = os.path.exists(log_file)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['datetime', 'label', 'x', 'y', 'width', 'height', 'score'])
        for name, x, y, w, h, score in detections_data:
            writer.writerow([timestamp, name, int(x), int(y), int(w), int(h), f"{score:.2f}"])


def log_signal_tower(detections_data):
    """シグナルタワー: 中心座標、色、点灯/点滅、IDを記録"""
    global st_color_history, st_blink_start_time
    log_file = get_log_file_path()
    file_exists = os.path.exists(log_file)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_log = time.time()

    with open(log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['datetime', 'id', 'center_x', 'center_y', 'color', 'state'])
        for center_x, center_y, color_name, region_id in detections_data:
            grid_x = center_x // GRID_SIZE
            grid_y = center_y // GRID_SIZE
            key = (grid_x, grid_y, color_name)
            history = st_color_history.get(key, deque())
            if len(history) >= 45:
                false_count = sum(1 for v in history if not v)
                state = "blink" if false_count > len(history) * 0.2 else "on"
            else:
                state = "off"
            writer.writerow([timestamp, region_id if region_id else '-', center_x, center_y, color_name, state])


def get_change_log_file_path():
    """変化ログファイルパスを返す"""
    date_str = datetime.now().strftime("%Y%m%d")
    return os.path.join(LOG_DIR, f"log_changes_{MODEL_TYPE}_{date_str}.csv")


def log_signal_change(region_id, color, old_state, new_state):
    """シグナルタワー: ID枠内の状態変化（点灯/点滅/消灯）を変化ログに記録"""
    log_file = get_change_log_file_path()
    file_exists = os.path.exists(log_file)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['datetime', 'id', 'color', 'old_state', 'new_state'])
        writer.writerow([timestamp, region_id, color, old_state, new_state])


def log_detection_change(label, event):
    """物体認識: 検出開始/消失を変化ログに記録"""
    log_file = get_change_log_file_path()
    file_exists = os.path.exists(log_file)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['datetime', 'label', 'event'])
        writer.writerow([timestamp, label, event])


# --- 送信用ログ（1日ごとローテーション、状態コードを記録） ---
def load_status_code_config():
    """状態コードのパターン定義を読み込む。
    形式: {"patterns": [{"conditions": {"red": "on", ...}, "code": 5}, ...], "default": 0}
    patternsは上から順に評価し、conditionsを全て満たす最初のパターンのcodeを採用する。"""
    try:
        if os.path.exists(STATUS_CODE_CONFIG_PATH):
            with open(STATUS_CODE_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                return cfg.get('patterns', []), cfg.get('default', 0)
    except Exception:
        pass
    return [], 0


def load_id_names():
    """ID名前マッピングを読み込む。{"1": ["名前1", "名前2", "名前3"], ...}"""
    try:
        if os.path.exists(ID_NAMES_CONFIG_PATH):
            with open(ID_NAMES_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f).get('id_names', {})
    except Exception:
        pass
    return {}


def id_name_parts(v):
    """ID名前の値を3項目の文字列リスト（前後空白除去）に正規化する。
    旧形式（単一文字列）も ["旧名", "", ""] に変換して後方互換を保つ。"""
    if isinstance(v, list):
        parts = [('' if x is None else str(x)).strip() for x in v][:3]
    else:
        parts = ['' if v is None else str(v).strip()]
    while len(parts) < 3:
        parts.append('')
    return parts


def compute_status_code(id_states, patterns, default_code):
    """id_states: {color: 'on'|'blink'|'off'}（未検出色はoff扱い）。
    patternsを順に評価し、conditionsを全て満たす最初のパターンのcodeを返す。
    どれも一致しなければdefault_code。"""
    for pat in patterns:
        conds = pat.get('conditions', {})
        if conds and all(id_states.get(c, 'off') == s for c, s in conds.items()):
            return pat.get('code', default_code)
    return default_code


class SendLogger:
    """名前付きIDの状態コードを送信用ログ（IP+日付.csv）に記録する。
    1日ごとにファイルをローテーションし、各IDの状態コードが変化したときだけ
    1行（日時YYYYMMDDhhmmss, 名前1, 名前2, 名前3, 状態コード）を追記する。"""
    def __init__(self):
        self.cur_file = None
        self.cur_window = None
        self.prev_codes = {}   # region_id -> 最後に記録した状態コード
        self._cfg_ts = 0.0
        self.patterns = []
        self.default_code = 0
        self.id_names = {}

    def _reload_cfg(self, now):
        # 状態コード定義・ID名前は5秒キャッシュ（Web編集を反映）
        if now - self._cfg_ts > 5.0:
            self._cfg_ts = now
            self.patterns, self.default_code = load_status_code_config()
            self.id_names = load_id_names()

    def update(self, now, st_states):
        """st_states: {(region_id, color): 'on'|'blink'|'off'}（st_change_prev_state）"""
        self._reload_cfg(now)
        # ローカル日付（YYYYMMDD）を区切りとし、1日ごとにファイルをローテーションする
        window = time.strftime('%Y%m%d', time.localtime(now))
        if window != self.cur_window:
            # 日付が変わった → 新ファイル。全名前付きIDの初期状態を書き出すため
            # prev_codesをクリアし、以降の比較で初回行を必ず出力する。
            self.cur_window = window
            ip = _get_ip_cached().replace(':', '-').replace('.', '-')
            fname = f"T1_{ip}_{window}.csv"
            self.cur_file = os.path.join(SEND_LOG_DIR, fname)
            self.prev_codes = {}

        # 名前が設定済みのIDのみ対象（3項目のうち1つでも入力があれば対象）
        named = {}
        for k, v in self.id_names.items():
            parts = id_name_parts(v)
            if any(parts):
                try:
                    named[int(k)] = parts
                except (ValueError, TypeError):
                    pass
        if not named:
            return

        # region_idごとに色状態を集約
        per_id = {}
        for (rid, color), state in st_states.items():
            if rid in named:
                per_id.setdefault(rid, {})[color] = state

        ts = time.strftime('%Y%m%d%H%M%S', time.localtime(now))
        rows = []
        for rid, parts in named.items():
            code = compute_status_code(per_id.get(rid, {}), self.patterns, self.default_code)
            if self.prev_codes.get(rid) != code:
                # 日時, 名前1, 名前2, 名前3, 状態コード
                rows.append([ts] + parts + [code])
                self.prev_codes[rid] = code
        if rows:
            try:
                with open(self.cur_file, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    for r in rows:
                        writer.writerow(r)
            except Exception as e:
                print(f"[SENDLOG] 送信用ログ書き込みエラー: {e}")


send_logger = SendLogger()


# --- 初期化 ---
# imagesフォルダ作成
if not os.path.exists("/home/pi/images"):
    os.makedirs("/home/pi/images")

# logsフォルダ作成
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# raw_imagesフォルダ作成
if not os.path.exists(RAW_IMAGE_DIR):
    os.makedirs(RAW_IMAGE_DIR)

# send_logsフォルダ作成（送信用ログ）
if not os.path.exists(SEND_LOG_DIR):
    os.makedirs(SEND_LOG_DIR)

# 認識設定を読み込み
recognition_config = load_recognition_config()
RECOGNITION_ENABLED = recognition_config.get('enabled', True)
RESOLUTION_SETTING = recognition_config.get('resolution', 'full')
CAMERA_RESOLUTION = recognition_config.get('camera_resolution', '1920x1080')

# カメラ出力解像度（起動時固定）
CAMERA_RES_MAP = {
    '480x320': (480, 320),
    '640x480': (640, 480),
    '640x640': (640, 640),
    '1280x720': (1280, 720),
    '1920x1080': (1920, 1080),
}
# 表示用解像度（LCD・HDMI・web画像に使用）
disp_w, disp_h = CAMERA_RES_MAP.get(CAMERA_RESOLUTION, (1280, 720))

# クロッププリセット（センサー座標でのクロップサイズ）
# None = クロップなし（最大画角）
RESOLUTION_MAP = {
    'full': None,
    '1920x1080': (1920, 1080),
    '1280x720': (1280, 720),
    '960x540': (960, 540),
    '640x480': (640, 480),
    '640x640': (640, 640),
    '480x320': (480, 320),
}

if RECOGNITION_ENABLED:
    # --- 認識ONモード ---
    imx500 = IMX500(MODEL_PATH)
    intrinsics = imx500.network_intrinsics
    if not intrinsics:
        intrinsics = NetworkIntrinsics()
        intrinsics.task = "object detection"

    # ラベル設定
    if os.path.exists(LABELS_PATH):
        with open(LABELS_PATH, 'r') as f:
            intrinsics.labels = f.read().splitlines()
    else:
        intrinsics.labels = []

    intrinsics.inference_resolution = (640, 640)

    intrinsics.inference_rate = 15
    fps = 15

    # Picamera2初期化
    picam2 = Picamera2(imx500.camera_num)

    # センサーモードから使用可能な最高解像度を選択
    # RAWストリームが必要なため Pi Zero 2W のメモリ制約: (RGB+RAW)×buffer ≤ ~100MB
    _MEM_LIMIT = 100 * 1024 * 1024
    _BUFS = 4
    _modes = sorted(picam2.sensor_modes, key=lambda m: m['size'][0] * m['size'][1], reverse=True)
    _best = next(
        (m for m in _modes
         if (m['size'][0] * m['size'][1] * 3 + m['size'][0] * m['size'][1] * 10 // 8) * _BUFS <= _MEM_LIMIT),
        _modes[-1]
    )
    if CAMERA_RESOLUTION in ('640x640', '1920x1080'):
        im_width, im_height = disp_w, disp_h
    else:
        im_width, im_height = _best['size'][0], _best['size'][1]

    # 推論(main)と表示(lores)を分離: main=センサー最大, lores=指定解像度
    _use_lores = CAMERA_RESOLUTION not in ('640x640', '1920x1080') and (disp_w, disp_h) != (im_width, im_height)
    if _use_lores:
        # loresサイズをmainのアスペクト比に合わせて補正（横幅基準、高さを調整）
        _mar = im_width / im_height
        if abs(_mar - disp_w / disp_h) > 0.01:
            disp_h = (int(disp_w / _mar) // 2) * 2

    print(f"モデル: {MODEL_PATH}")
    print(f"ラベル数: {len(intrinsics.labels)}")
    print(f"タスク: {intrinsics.task}")
    print(f"推論レート: {intrinsics.inference_rate} FPS")
    print(f"認識ONモード: 推論={im_width}x{im_height}, 表示={disp_w}x{disp_h}, FPS={fps}")
    # 直接指定解像度(FHD/640x640)はbuffer_count=6、センサー最大経由はメモリ予算値を使用
    _buf_count = 6 if CAMERA_RESOLUTION in ('640x640', '1920x1080') else _BUFS
    if _use_lores:
        config = picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (im_width, im_height)},
            lores={"format": "YUV420", "size": (disp_w, disp_h)},
            display="lores",
            controls={"FrameRate": fps},
            buffer_count=_buf_count
        )
    else:
        config = picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (im_width, im_height)},
            controls={"FrameRate": fps},
            buffer_count=_buf_count
        )
    print(f"カメラ設定: {config}")
    picam2.configure(config)
    imx500.show_network_fw_progress_bar()

else:
    # --- 認識OFFモード（IMX500ファームウェア転送をスキップ）---
    fps = 30
    picam2 = Picamera2(0)

    # センサーモードから使用可能な最高解像度を選択（RAWなし、制限緩）
    _MEM_LIMIT_OFF = 150 * 1024 * 1024
    _BUFS_OFF = 2
    _modes_off = sorted(picam2.sensor_modes, key=lambda m: m['size'][0] * m['size'][1], reverse=True)
    _best_off = next(
        (m for m in _modes_off
         if m['size'][0] * m['size'][1] * 3 * _BUFS_OFF <= _MEM_LIMIT_OFF),
        _modes_off[-1]
    )
    if CAMERA_RESOLUTION in ('640x640', '1920x1080'):
        im_width, im_height = disp_w, disp_h
    else:
        im_width, im_height = _best_off['size'][0], _best_off['size'][1]

    _use_lores = CAMERA_RESOLUTION not in ('640x640', '1920x1080') and (disp_w, disp_h) != (im_width, im_height)
    if _use_lores:
        # loresサイズをmainのアスペクト比に合わせて補正（横幅基準、高さを調整）
        _mar = im_width / im_height
        if abs(_mar - disp_w / disp_h) > 0.01:
            disp_h = (int(disp_w / _mar) // 2) * 2
    print(f"認識OFFモード: 推論={im_width}x{im_height}, 表示={disp_w}x{disp_h}, FPS={fps}")

    if _use_lores:
        config = picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (im_width, im_height)},
            lores={"format": "YUV420", "size": (disp_w, disp_h)},
            display="lores",
            controls={"FrameRate": fps},
            buffer_count=6 if CAMERA_RESOLUTION in ('640x640', '1920x1080') else _BUFS_OFF
        )
    else:
        config = picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (im_width, im_height)},
            controls={"FrameRate": fps},
            buffer_count=6 if CAMERA_RESOLUTION in ('640x640', '1920x1080') else _BUFS_OFF
        )
    # RAWストリームを無効化してDMAメモリを節約（Pi Zero 2W対策）
    config["raw"] = None
    print(f"カメラ設定: {config}")
    picam2.configure(config)

# DRMプレビュースレッドが "Failed to reserve DRM plane" でクラッシュしないよう保護
# Pi Zero 2Wではファームウェア転送中に一時的なプレーン競合が起きることがある
try:
    import picamera2.previews.drm_preview as _drm_mod
    _orig_render_drm = _drm_mod.DrmPreview.render_drm
    def _safe_render_drm(self, picam2_obj, completed_request):
        try:
            _orig_render_drm(self, picam2_obj, completed_request)
        except RuntimeError as e:
            if "Failed to reserve DRM plane" in str(e):
                pass  # 一時的な競合はスキップ（次フレームで再試行）
            else:
                raise
    _drm_mod.DrmPreview.render_drm = _safe_render_drm
    print("DRMプレビュー保護パッチ適用済み")
except Exception as _e:
    print(f"DRMパッチ適用スキップ: {_e}")

# HDMI出力（DRMプレビュー + set_overlay方式）
HDMI_ENABLED = False
HDMI_DISPLAY_W = 0
HDMI_DISPLAY_H = 0

def get_display_resolution():
    """HDMI接続モニターの解像度を取得"""
    for card_path in ['/sys/class/drm/card0-HDMI-A-1/modes',
                      '/sys/class/drm/card1-HDMI-A-1/modes',
                      '/sys/class/drm/card0-HDMI-A-2/modes',
                      '/sys/class/drm/card1-HDMI-A-2/modes']:
        try:
            with open(card_path, 'r') as f:
                modes = f.read().strip().split('\n')
                if modes and modes[0]:
                    parts = modes[0].split('x')
                    w = int(parts[0])
                    # "1080" or "1080p60" etc → 数字部分のみ
                    h_str = ''.join(c for c in parts[1] if c.isdigit())
                    h = int(h_str)
                    if w > 0 and h > 0:
                        return w, h
        except Exception:
            continue
    return 1280, 720  # デフォルト


def calc_drm_window(cam_w, cam_h, disp_w, disp_h):
    """カメラのアスペクト比を維持するDRM表示ウィンドウ座標を計算する。
    モニター内にレターボックス/ピラーボックスで収める。"""
    if cam_w * disp_h > disp_w * cam_h:
        # カメラが表示より横長: 上下に黒帯
        fit_w = disp_w
        fit_h = disp_w * cam_h // cam_w
        x = 0
        y = (disp_h - fit_h) // 2
    else:
        # カメラが表示より縦長または同比: 左右に黒帯
        fit_h = disp_h
        fit_w = disp_h * cam_w // cam_h
        x = (disp_w - fit_w) // 2
        y = 0
    return x, y, fit_w, fit_h


try:
    HDMI_DISPLAY_W, HDMI_DISPLAY_H = get_display_resolution()
    _dx, _dy, _dw, _dh = calc_drm_window(disp_w, disp_h, HDMI_DISPLAY_W, HDMI_DISPLAY_H)
    picam2.start_preview(Preview.DRM, x=_dx, y=_dy, width=_dw, height=_dh)
    HDMI_ENABLED = True
    print(f"HDMI出力: DRMプレビュー有効 (推論={im_width}x{im_height} 表示={disp_w}x{disp_h} → DRM{_dw}x{_dh} offset({_dx},{_dy}))")
except Exception as e:
    print(f"HDMI出力: DRMプレビュー無効 ({e})")

# SPI LCD 初期化 (OSOYOO 3.5" 480x320)
_LCD_W, _LCD_H = 480, 320

# LCD事前ダウンスケール用定数
# カメラ設定解像度（disp_h調整前）がLCDと一致する場合は全画面表示（アスペクト比調整分をstretchで吸収）
_cam_res_orig = CAMERA_RES_MAP.get(CAMERA_RESOLUTION, (disp_w, disp_h))
if _cam_res_orig[0] == _LCD_W and _cam_res_orig[1] == _LCD_H:
    _LCD_NEW_W, _LCD_NEW_H = _LCD_W, _LCD_H
    _LCD_SCALE = 1.0
else:
    _LCD_SCALE = min(_LCD_W / disp_w, _LCD_H / disp_h)
    _LCD_NEW_W = int(disp_w * _LCD_SCALE)
    _LCD_NEW_H = int(disp_h * _LCD_SCALE)
# 640×640: 正方形画像を左寄せ、右の余白に縦ボタン配置
_side_btns = (_LCD_NEW_W < _LCD_W and _LCD_NEW_H >= _LCD_H - 1)
_LCD_OFF_X = 0 if _side_btns else ((_LCD_W - _LCD_NEW_W) // 2)
# FHDは画像を上端に寄せて下の余白をボタン領域に使用
_LCD_OFF_Y = 0 if disp_w >= 1920 else (_LCD_H - _LCD_NEW_H) // 2

# ===== タッチパネル (XPT2046 ハードウェアSPI bus0 dev1) =====
# datasheet: Pin11=TP_IRQ(GPIO17), Pin19=TP_SI(MOSI), Pin21=TP_SO(MISO),
#            Pin23=TP_SCK, Pin26=TP_CS(CE1=GPIO7)
_TOUCH_IRQ = 17  # TP_IRQ: GPIO17 (物理ピン11)
# OSOYOO 3.5" 480x320 (ILI9488 MADCTL=0x28 landscape):
#   XPT2046 X+(0xD0) → sy (短辺320方向), Y+(0x90) → sx (長辺480方向)
#   sy 方向は反転あり（画面右端 = raw_x 小、左端 = raw_x 大）
_TOUCH_X_MIN, _TOUCH_X_MAX = 200, 3900  # raw_x (0xD0=X+) → sy方向
_TOUCH_Y_MIN, _TOUCH_Y_MAX = 200, 3900  # raw_y (0x90=Y+) → sx方向

# タッチUIボタン定義（各ボタンに x, y, w, h を持つ）
if _side_btns:
    # 640×640: 右エリア(160px)を2列に分割 (各列80px)
    _BTN_COL_W  = (_LCD_W - _LCD_NEW_W) // 2  # 80
    _BTN_COL1_X = _LCD_NEW_W                   # 320 (左列: _TOP_BTNS)
    _BTN_COL2_X = _LCD_NEW_W + _BTN_COL_W     # 400 (右列: _BTNS)
    _bh = _LCD_H // 5                          # 64
    _BTNS = [
        {'id': 'crop-',    'label': 'C-',  'x': _BTN_COL2_X, 'y': 0,       'w': _BTN_COL_W, 'h': _bh},
        {'id': 'crop+',    'label': 'C+',  'x': _BTN_COL2_X, 'y': _bh,     'w': _BTN_COL_W, 'h': _bh},
        {'id': 'id',       'label': 'ID',  'x': _BTN_COL2_X, 'y': _bh * 2, 'w': _BTN_COL_W, 'h': _bh},
        {'id': 'rst',      'label': 'RST', 'x': _BTN_COL2_X, 'y': _bh * 3, 'w': _BTN_COL_W, 'h': _bh},
        {'id': 'shutdown', 'label': 'OFF', 'x': _BTN_COL2_X, 'y': _bh * 4, 'w': _BTN_COL_W, 'h': _LCD_H - _bh * 4},
    ]
else:
    # FHD/その他: 下端に横5ボタン
    _BTN_H = _LCD_H - _LCD_NEW_H if disp_w >= 1920 else 60
    _BTN_Y = _LCD_NEW_H           if disp_w >= 1920 else _LCD_H - _BTN_H
    _bw    = _LCD_W // 5
    _BTNS = [
        {'id': 'crop-',    'label': 'C-',  'x': 0,       'y': _BTN_Y, 'w': _bw,           'h': _BTN_H},
        {'id': 'crop+',    'label': 'C+',  'x': _bw,     'y': _BTN_Y, 'w': _bw,           'h': _BTN_H},
        {'id': 'id',       'label': 'ID',  'x': _bw * 2, 'y': _BTN_Y, 'w': _bw,           'h': _BTN_H},
        {'id': 'rst',      'label': 'RST', 'x': _bw * 3, 'y': _BTN_Y, 'w': _bw,           'h': _BTN_H},
        {'id': 'shutdown', 'label': 'OFF', 'x': _bw * 4, 'y': _BTN_Y, 'w': _LCD_W-_bw*4, 'h': _BTN_H},
    ]

# カメラ設定解像度がLCDと同サイズ（全画面）の場合はボタンが映像に重なるため透明背景
_transparent_btns = (_cam_res_orig[0] == _LCD_W and _cam_res_orig[1] == _LCD_H)

# 上部ボタン: モデル切り替え(OBJ/SIG) + 解像度切り替え(640/FHD) + 保存切り替え(SVR/RAW)
# カメラ映像に重なるため常に透明背景
_TOP_BTN_H = 60  # 横並び時のボタン高さ
if _side_btns:
    # 640×640: 右エリア左列に縦6ボタン（映像の右に配置）
    _TOP_BTN_W = _BTN_COL_W  # 80
    _top_bh = _LCD_H // 6    # 53px
    _TOP_BTNS = [
        {'id': 'model_obj',   'label': 'OBJ', 'x': _BTN_COL1_X, 'y': 0,            'w': _TOP_BTN_W, 'h': _top_bh},
        {'id': 'model_sig',   'label': 'SIG', 'x': _BTN_COL1_X, 'y': _top_bh,      'w': _TOP_BTN_W, 'h': _top_bh},
        {'id': 'res_640',     'label': '640', 'x': _BTN_COL1_X, 'y': _top_bh * 2,  'w': _TOP_BTN_W, 'h': _top_bh},
        {'id': 'res_fhd',     'label': 'FHD', 'x': _BTN_COL1_X, 'y': _top_bh * 3,  'w': _TOP_BTN_W, 'h': _top_bh},
        {'id': 'save_result', 'label': 'SVR', 'x': _BTN_COL1_X, 'y': _top_bh * 4,  'w': _TOP_BTN_W, 'h': _top_bh},
        {'id': 'save_raw',    'label': 'RAW', 'x': _BTN_COL1_X, 'y': _top_bh * 5,  'w': _TOP_BTN_W, 'h': _LCD_H - _top_bh * 5},
    ]
else:
    # その他: 上端に横6ボタン
    _top_bw = _LCD_W // 6  # 80px
    _TOP_BTNS = [
        {'id': 'model_obj',   'label': 'OBJ', 'x': 0,           'y': 0, 'w': _top_bw,           'h': _TOP_BTN_H},
        {'id': 'model_sig',   'label': 'SIG', 'x': _top_bw,     'y': 0, 'w': _top_bw,           'h': _TOP_BTN_H},
        {'id': 'res_640',     'label': '640', 'x': _top_bw * 2, 'y': 0, 'w': _top_bw,           'h': _TOP_BTN_H},
        {'id': 'res_fhd',     'label': 'FHD', 'x': _top_bw * 3, 'y': 0, 'w': _top_bw,           'h': _TOP_BTN_H},
        {'id': 'save_result', 'label': 'SVR', 'x': _top_bw * 4, 'y': 0, 'w': _top_bw,           'h': _TOP_BTN_H},
        {'id': 'save_raw',    'label': 'RAW', 'x': _top_bw * 5, 'y': 0, 'w': _LCD_W-_top_bw*5, 'h': _TOP_BTN_H},
    ]
_TOP_RES_MAP = {'res_640': '640x640', 'res_fhd': '1920x1080'}
_TOP_MODEL_PATHS = {
    'model_obj': ('/home/pi/object_detection/network.rpk', '/home/pi/object_detection/labels.txt'),
    'model_sig': ('/home/pi/signal_tower/network.rpk',     '/home/pi/signal_tower/labels.txt'),
}

_touch_irq_line = None
_touch_spi_dev = None
_touch_ok = False
_touch_last_action = 0.0
_shutdown_confirm = False
_shutdown_in_progress = False   # タッチでシャットダウン実行後True（LCDをシャットダウン画面に切替）
_shutdown_started_at = 0.0      # シャットダウン実行時刻（15秒後にLCDを真っ黒にする）
_CONFIRM_YES = {'x': 100, 'y': 170, 'w': 110, 'h': 50}
_CONFIRM_NO  = {'x': 270, 'y': 170, 'w': 110, 'h': 50}
_pending_confirm = None  # モデル/解像度切り替え確認待ち (btn_id or None)
_CONFIRM_MSGS = {
    'model_obj': 'MODEL: OBJ?',
    'model_sig': 'MODEL: SIG?',
    'res_640':   'RES: 640?',
    'res_fhd':   'RES: FHD?',
}

# --- IP長押しによるWPS起動 ---
_IP_LONGPRESS_SEC = 2.0          # IP表示部の長押し判定秒数
_ip_touch_box = None             # IP表示部の当たり判定 (x, y, w, h)。draw_touch_buttonsで更新
_ip_press_start = 0.0            # IP領域内タッチの押下開始時刻（離すと0）
_ip_longpress_fired = False      # 今回の押下で既にWPSを起動済みか
_wps_lock = threading.Lock()
_wps_active = False              # WPS実行中
_wps_msg = ''                    # LCDに表示するWPS状態メッセージ
_wps_msg_until = 0.0             # メッセージ表示終了時刻

def _set_wps_msg(msg, dur):
    """LCDに表示するWPS状態メッセージを設定（dur秒間表示）"""
    global _wps_msg, _wps_msg_until
    _wps_msg = msg
    _wps_msg_until = time.time() + dur

def _trigger_wps():
    """WPS(PBC)をバックグラウンドで起動。多重起動はガードする。"""
    global _wps_active
    with _wps_lock:
        if _wps_active:
            return
        _wps_active = True
    threading.Thread(target=_wps_worker, daemon=True).start()

def _wps_worker():
    """wpa_cli wps_pbc でWPSプッシュボタン接続を開始し、接続確立を最大120秒監視する。"""
    global _wps_active
    import subprocess
    try:
        def _iwgetid():
            try:
                r = subprocess.run(['iwgetid', '-r'], capture_output=True,
                                   text=True, timeout=5)
                return r.stdout.strip()
            except Exception:
                return ''
        before_ssid = _iwgetid()
        _set_wps_msg('WPS: push router button', 130)
        # WPS PBCを開始（wpa_supplicant直接管理時に有効。NetworkManager運用では
        # 失敗する場合がある）
        try:
            r = subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'wps_pbc'],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0 or 'OK' not in (r.stdout or ''):
                _set_wps_msg('WPS not available', 8)
                return
        except FileNotFoundError:
            _set_wps_msg('WPS not available', 8)
            return
        except Exception:
            _set_wps_msg('WPS error', 8)
            return
        # 接続確立（SSIDが付く）を最大120秒ポーリング
        deadline = time.time() + 120
        connected = False
        while time.time() < deadline:
            ssid = _iwgetid()
            if ssid and ssid != before_ssid:
                connected = True
                break
            _set_wps_msg('WPS connecting...', 130)
            time.sleep(3)
        if connected:
            # 認証情報を wpa_supplicant.conf に永続化（update_config=1時）
            try:
                subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'save_config'],
                               capture_output=True, timeout=10)
            except Exception:
                pass
            _ip_cache['ts'] = 0.0   # IPキャッシュを即時更新させる
            _set_wps_msg('WPS OK', 8)
        else:
            _set_wps_msg('WPS timeout', 8)
    finally:
        _wps_active = False

def _touch_read_raw():
    """XPT2046から生座標を読み取る（ハードウェアSPI）。タッチなし or 範囲外はNone。"""
    if _touch_irq_line and _touch_irq_line.get_value() == 1:
        return None
    # XPT2046: 3バイトxfer2でコマンド送信＋12bit結果取得 (((b1<<8)|b2)>>3)
    # 0xD0 = X+チャネル (A2A1A0=101), 0x90 = Y+チャネル (A2A1A0=001)
    # 安定化のため3回平均
    xs, ys = [], []
    for _ in range(3):
        rx = _touch_spi_dev.xfer2([0xD0, 0x00, 0x00])
        ry = _touch_spi_dev.xfer2([0x90, 0x00, 0x00])
        xs.append(((rx[1] << 8) | rx[2]) >> 3)
        ys.append(((ry[1] << 8) | ry[2]) >> 3)
    x = int(sum(xs) / 3)
    y = int(sum(ys) / 3)
    if x < 100 or x > 3900 or y < 100 or y > 3900:
        return None
    return x, y

def _touch_to_screen(raw_x, raw_y):
    """生座標 → LCD画面座標 (0-479, 0-319)
    OSOYOO 3.5" 480x320 landscape (MADCTL=0x28):
      sx: Y+チャネル (0x90) → 水平方向 (0-479), 反転なし
      sy: X+チャネル (0xD0) → 垂直方向 (0-319), 反転あり
    """
    sx = int((raw_y - _TOUCH_Y_MIN) * _LCD_W / (_TOUCH_Y_MAX - _TOUCH_Y_MIN))
    sy = _LCD_H - 1 - int((raw_x - _TOUCH_X_MIN) * _LCD_H / (_TOUCH_X_MAX - _TOUCH_X_MIN))
    return max(0, min(_LCD_W - 1, sx)), max(0, min(_LCD_H - 1, sy))

def _handle_touch_btn(btn_id):
    """タッチボタンのアクション処理（500msデバウンス）"""
    global _touch_last_action, _pending_confirm
    now = time.time()
    if now - _touch_last_action < 0.5:
        return
    _touch_last_action = now
    if btn_id == 'rec':
        rec = load_recognition_config()
        rec['enabled'] = not rec.get('enabled', True)
        with open(RECOGNITION_CONFIG_PATH, 'w') as f:
            json.dump(rec, f)
        print(f"[TOUCH] 認識: {'ON' if rec['enabled'] else 'OFF'}")
    elif btn_id == 'save':
        cfg = load_save_config()
        cfg['save_result'] = not cfg.get('save_result', True)
        with open(SAVE_CONFIG_PATH, 'w') as f:
            json.dump(cfg, f)
        print(f"[TOUCH] 保存: {'ON' if cfg['save_result'] else 'OFF'}")
    elif btn_id == 'thr-':
        t = round(max(0.05, THRESHOLD - 0.05), 2)
        with open(THRESHOLD_CONFIG_PATH, 'w') as f:
            json.dump({'threshold': t}, f)
        print(f"[TOUCH] しきい値: {t}")
    elif btn_id == 'thr+':
        t = round(min(0.95, THRESHOLD + 0.05), 2)
        with open(THRESHOLD_CONFIG_PATH, 'w') as f:
            json.dump({'threshold': t}, f)
        print(f"[TOUCH] しきい値: {t}")
    elif btn_id in ('crop-', 'crop+'):
        _CROP_STEPS = [10, 12, 17, 24, 34, 49, 70, 100]
        rec = load_recognition_config()
        try: val = int(rec.get('resolution', '100'))
        except: val = 100
        # 現在値に最も近いステップのインデックスを探す
        idx = min(range(len(_CROP_STEPS)), key=lambda i: abs(_CROP_STEPS[i] - val))
        if btn_id == 'crop-':
            idx = max(0, idx - 1)
        else:
            idx = min(len(_CROP_STEPS) - 1, idx + 1)
        rec['resolution'] = str(_CROP_STEPS[idx])
        with open(RECOGNITION_CONFIG_PATH, 'w') as f:
            json.dump(rec, f)
        print(f"[TOUCH] クロップ: {rec['resolution']}%")
    elif btn_id == 'id':
        open(SIGNAL_ID_TRIGGER_PATH, 'w').close()
        print("[TOUCH] ID設定トリガー")
    elif btn_id == 'rst':
        open(SIGNAL_ID_RESET_PATH, 'w').close()
        print("[TOUCH] IDリセットトリガー")
    elif btn_id == 'shutdown':
        global _shutdown_confirm
        _shutdown_confirm = True
        print("[TOUCH] シャットダウン確認画面表示")
    elif btn_id in _TOP_RES_MAP:
        _pending_confirm = btn_id
        print(f"[TOUCH] 解像度切り替え確認: {_TOP_RES_MAP[btn_id]}")
    elif btn_id in _TOP_MODEL_PATHS:
        _pending_confirm = btn_id
        print(f"[TOUCH] モデル切り替え確認: {btn_id}")
    elif btn_id == 'save_result':
        cfg = load_save_config()
        cfg['save_result'] = not cfg.get('save_result', True)
        with open(SAVE_CONFIG_PATH, 'w') as f:
            json.dump(cfg, f)
        print(f"[TOUCH] 結果保存: {'ON' if cfg['save_result'] else 'OFF'}")
    elif btn_id == 'save_raw':
        cfg = load_save_config()
        cfg['save_raw'] = not cfg.get('save_raw', True)
        with open(SAVE_CONFIG_PATH, 'w') as f:
            json.dump(cfg, f)
        print(f"[TOUCH] 生画像保存: {'ON' if cfg['save_raw'] else 'OFF'}")

def _execute_confirmed_action(btn_id):
    """確認ダイアログでYESが押された後の実際の処理（モデル/解像度切り替え）"""
    if btn_id in _TOP_RES_MAP:
        new_res = _TOP_RES_MAP[btn_id]
        rec = load_recognition_config()
        rec['camera_resolution'] = new_res
        with open(RECOGNITION_CONFIG_PATH, 'w') as f:
            json.dump(rec, f)
        print(f"[TOUCH] 解像度切り替え: {new_res}")
    elif btn_id in _TOP_MODEL_PATHS:
        net, lbl = _TOP_MODEL_PATHS[btn_id]
        with open(MODEL_CONFIG_PATH, 'w') as f:
            json.dump({'network': net, 'labels': lbl}, f)
        print(f"[TOUCH] モデル切り替え: {btn_id} → {net}")


def _touch_loop():
    """タッチパネル読み取りスレッド（50msポーリング）"""
    global _shutdown_confirm, _pending_confirm, _touch_last_action
    global _shutdown_in_progress, _shutdown_started_at
    global _ip_press_start, _ip_longpress_fired
    while True:
        try:
            raw = _touch_read_raw()
            if raw:
                sx, sy = _touch_to_screen(*raw)
                if _shutdown_confirm:
                    now = time.time()
                    if now - _touch_last_action >= 0.5:
                        yes = _CONFIRM_YES
                        no  = _CONFIRM_NO
                        if (yes['x'] <= sx < yes['x'] + yes['w'] and
                                yes['y'] <= sy < yes['y'] + yes['h']):
                            _touch_last_action = now
                            print("[TOUCH] シャットダウン実行")
                            # Web版と同様に「SHUTTING DOWN...」を表示し、
                            # 15秒後にLCDを真っ黒にする（即時の表示OFFはしない）
                            _shutdown_confirm = False
                            _shutdown_in_progress = True
                            _shutdown_started_at = now
                            import subprocess
                            subprocess.Popen(['sudo', 'shutdown', '-h', 'now'])
                        elif (no['x'] <= sx < no['x'] + no['w'] and
                                no['y'] <= sy < no['y'] + no['h']):
                            _shutdown_confirm = False
                            _touch_last_action = now
                            print("[TOUCH] シャットダウンキャンセル")
                elif _pending_confirm is not None:
                    now = time.time()
                    if now - _touch_last_action >= 0.5:
                        yes = _CONFIRM_YES
                        no  = _CONFIRM_NO
                        if (yes['x'] <= sx < yes['x'] + yes['w'] and
                                yes['y'] <= sy < yes['y'] + yes['h']):
                            _touch_last_action = now
                            action = _pending_confirm
                            _pending_confirm = None
                            _execute_confirmed_action(action)
                        elif (no['x'] <= sx < no['x'] + no['w'] and
                                no['y'] <= sy < no['y'] + no['h']):
                            action = _pending_confirm
                            _pending_confirm = None
                            _touch_last_action = now
                            print(f"[TOUCH] キャンセル: {action}")
                else:
                    box = _ip_touch_box
                    now = time.time()
                    in_ip = (box is not None and
                             box[0] <= sx < box[0] + box[2] and
                             box[1] <= sy < box[1] + box[3])
                    if in_ip:
                        # IP表示部の長押し → WPS起動（押下継続をポーリングで計測）
                        if _ip_press_start == 0.0:
                            _ip_press_start = now
                            _ip_longpress_fired = False
                        elif (not _ip_longpress_fired and
                                now - _ip_press_start >= _IP_LONGPRESS_SEC):
                            _ip_longpress_fired = True
                            print("[TOUCH] IP長押し → WPS起動")
                            _trigger_wps()
                    else:
                        _ip_press_start = 0.0
                        for btn in _TOP_BTNS + _BTNS:
                            if (btn['x'] <= sx < btn['x'] + btn['w'] and
                                    btn['y'] <= sy < btn['y'] + btn['h']):
                                _handle_touch_btn(btn['id'])
                                break
            else:
                # タッチが離れたら長押し計測をリセット
                _ip_press_start = 0.0
                _ip_longpress_fired = False
            time.sleep(0.05)
        except Exception as e:
            print(f"[TOUCH] エラー: {e}")
            time.sleep(0.1)

def draw_touch_buttons(frame_lcd):
    """LCDフレームにタッチボタンバーを描画"""
    save_cfg = config_cache.get('save_config', {})
    rec_on   = config_cache.get('recognition_enabled', True)
    bx1 = min(b['x'] for b in _BTNS)
    by1 = min(b['y'] for b in _BTNS)
    bx2 = max(b['x'] + b['w'] for b in _BTNS) - 1
    by2 = max(b['y'] + b['h'] for b in _BTNS) - 1
    if not _transparent_btns:
        if _side_btns:
            # 右エリア2列全体を背景塗り（_TOP_BTNSも含む）
            cv2.rectangle(frame_lcd, (_LCD_NEW_W, 0), (_LCD_W - 1, _LCD_H - 1), (40, 40, 40), -1)
        else:
            cv2.rectangle(frame_lcd, (bx1, by1), (bx2, by2), (40, 40, 40), -1)
    try: crop_val = int(config_cache.get('resolution', '100'))
    except: crop_val = 100
    for btn in _BTNS:
        bid = btn['id']
        x1, y1 = btn['x'], btn['y']
        x2, y2 = x1 + btn['w'] - 1, y1 + btn['h'] - 1
        if bid == 'rec':
            bg = (0, 120, 0) if rec_on else (0, 0, 120)
            fg = (255, 255, 255); label = 'REC'
        elif bid == 'save':
            bg = (0, 100, 0) if save_cfg.get('save_result', True) else (60, 60, 60)
            fg = (255, 255, 255); label = 'SAV'
        elif bid == 'thr-':
            bg = (60, 60, 60); fg = (200, 200, 200); label = 'T-'
        elif bid == 'thr+':
            bg = (60, 60, 60); fg = (200, 200, 200); label = f'{THRESHOLD:.2f}'
        elif bid == 'crop-':
            bg = (60, 40, 0); fg = (200, 160, 80); label = 'C-'
        elif bid == 'crop+':
            bg = (60, 40, 0); fg = (200, 160, 80); label = 'C+'
        elif bid == 'id':
            bg = (80, 60, 0); fg = (255, 200, 0); label = 'ID'
        elif bid == 'rst':
            bg = (80, 0, 0); fg = (255, 100, 100); label = 'RST'
        else:  # shutdown
            bg = (100, 0, 80); fg = (255, 100, 220); label = 'OFF'
        if not _transparent_btns:
            cv2.rectangle(frame_lcd, (x1, y1), (x2, y2), bg, -1)
        cv2.rectangle(frame_lcd, (x1, y1), (x2, y2), (120, 120, 120), 1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        tx = x1 + (btn['w'] - tw) // 2
        ty = y1 + (btn['h'] + th) // 2
        if _transparent_btns:
            cv2.putText(frame_lcd, label, (tx + 1, ty + 1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)
        cv2.putText(frame_lcd, label, (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, fg, 1)

    # 上部ボタン描画（常に透明背景 + 影付きテキスト）
    for btn in _TOP_BTNS:
        bid = btn['id']
        x1, y1 = btn['x'], btn['y']
        x2, y2 = x1 + btn['w'] - 1, y1 + btn['h'] - 1
        label = btn['label']
        if bid == 'model_obj':
            fg = (255, 255, 0) if MODEL_TYPE == 'object_detection' else (180, 180, 180)
        elif bid == 'model_sig':
            fg = (255, 255, 0) if MODEL_TYPE == 'signal_tower' else (180, 180, 180)
        elif bid == 'save_result':
            fg = (0, 255, 100) if save_cfg.get('save_result', True) else (180, 180, 180)
        elif bid == 'save_raw':
            fg = (0, 255, 100) if save_cfg.get('save_raw', True) else (180, 180, 180)
        else:
            fg = (255, 255, 0) if CAMERA_RESOLUTION == _TOP_RES_MAP.get(bid, '') else (180, 180, 180)
        cv2.rectangle(frame_lcd, (x1, y1), (x2, y2), (100, 100, 100), 1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        tx = x1 + (btn['w'] - tw) // 2
        ty = y1 + (btn['h'] + th) // 2
        cv2.putText(frame_lcd, label, (tx + 1, ty + 1),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)
        cv2.putText(frame_lcd, label, (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, fg, 1)

    # シャットダウン確認ダイアログ
    if _shutdown_confirm:
        cv2.rectangle(frame_lcd, (80, 80), (400, 240), (30, 30, 30), -1)
        cv2.rectangle(frame_lcd, (80, 80), (400, 240), (200, 200, 200), 2)
        msg = "SHUTDOWN?"
        (mw, mh), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame_lcd, msg, (240 - mw // 2, 145),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        yes = _CONFIRM_YES
        cv2.rectangle(frame_lcd, (yes['x'], yes['y']),
                      (yes['x'] + yes['w'], yes['y'] + yes['h']), (0, 0, 140), -1)
        cv2.rectangle(frame_lcd, (yes['x'], yes['y']),
                      (yes['x'] + yes['w'], yes['y'] + yes['h']), (100, 100, 220), 1)
        (yw, yh), _ = cv2.getTextSize("YES", cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame_lcd, "YES",
                    (yes['x'] + (yes['w'] - yw) // 2, yes['y'] + (yes['h'] + yh) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (100, 100, 255), 2)
        no = _CONFIRM_NO
        cv2.rectangle(frame_lcd, (no['x'], no['y']),
                      (no['x'] + no['w'], no['y'] + no['h']), (0, 80, 0), -1)
        cv2.rectangle(frame_lcd, (no['x'], no['y']),
                      (no['x'] + no['w'], no['y'] + no['h']), (100, 220, 100), 1)
        (nw, nh), _ = cv2.getTextSize("NO", cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame_lcd, "NO",
                    (no['x'] + (no['w'] - nw) // 2, no['y'] + (no['h'] + nh) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (100, 255, 100), 2)

    elif _pending_confirm is not None:
        msg = _CONFIRM_MSGS.get(_pending_confirm, 'CONFIRM?')
        cv2.rectangle(frame_lcd, (80, 80), (400, 240), (30, 30, 30), -1)
        cv2.rectangle(frame_lcd, (80, 80), (400, 240), (200, 200, 200), 2)
        (mw, mh), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame_lcd, msg, (240 - mw // 2, 145),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        yes = _CONFIRM_YES
        cv2.rectangle(frame_lcd, (yes['x'], yes['y']),
                      (yes['x'] + yes['w'], yes['y'] + yes['h']), (0, 0, 140), -1)
        cv2.rectangle(frame_lcd, (yes['x'], yes['y']),
                      (yes['x'] + yes['w'], yes['y'] + yes['h']), (100, 100, 220), 1)
        (yw, yh), _ = cv2.getTextSize("YES", cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame_lcd, "YES",
                    (yes['x'] + (yes['w'] - yw) // 2, yes['y'] + (yes['h'] + yh) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (100, 100, 255), 2)
        no = _CONFIRM_NO
        cv2.rectangle(frame_lcd, (no['x'], no['y']),
                      (no['x'] + no['w'], no['y'] + no['h']), (0, 80, 0), -1)
        cv2.rectangle(frame_lcd, (no['x'], no['y']),
                      (no['x'] + no['w'], no['y'] + no['h']), (100, 220, 100), 1)
        (nw, nh), _ = cv2.getTextSize("NO", cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame_lcd, "NO",
                    (no['x'] + (no['w'] - nw) // 2, no['y'] + (no['h'] + nh) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (100, 255, 100), 2)

    # IPアドレスを上部中央に表示
    global _ip_touch_box
    ip_text = _get_ip_cached()
    (iw, ih), _ = cv2.getTextSize(ip_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    _ip_cx = _LCD_NEW_W // 2 if _side_btns else _LCD_W // 2
    _ip_x = max(0, _ip_cx - iw // 2)
    _ip_y = ih + 2
    cv2.putText(frame_lcd, ip_text, (_ip_x + 1, _ip_y + 1),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    cv2.putText(frame_lcd, ip_text, (_ip_x, _ip_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    # IP長押し(WPS)の当たり判定領域を更新（小さい文字なので上下左右に余白を付ける）
    _pad_x, _pad_y = 20, 10
    _ip_touch_box = (max(0, _ip_x - _pad_x), 0,
                     iw + 2 * _pad_x, _ip_y + _pad_y)

    # WPS状態メッセージをIPの下に表示
    if _wps_msg and time.time() < _wps_msg_until:
        (ww, wh), _ = cv2.getTextSize(_wps_msg, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        _w_x = max(0, _ip_cx - ww // 2)
        _w_y = _ip_y + wh + 8
        cv2.putText(frame_lcd, _wps_msg, (_w_x + 1, _w_y + 1),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        cv2.putText(frame_lcd, _wps_msg, (_w_x, _w_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

_lcd_spi = None
_lcd_dc = None
_lcd_queue = None
_last_lcd_time = 0.0
# 注: バックライトは電源直結のためソフトから消灯不可。シャットダウン時はピクセルを
# 黒にするのみ（バックライトは点灯のまま）。GPIO消灯は無効と確認したため実装しない。

if _LCD_HW_OK:
    try:
        _gc = _gpiod.Chip('gpiochip0')
        _lcd_dc  = _gc.get_line(24); _lcd_dc.request(consumer='lcd', type=_gpiod.LINE_REQ_DIR_OUT)
        _lcd_rst = _gc.get_line(25); _lcd_rst.request(consumer='lcd', type=_gpiod.LINE_REQ_DIR_OUT)
        _lcd_spi = _spidev.SpiDev(); _lcd_spi.open(0, 0)
        _lcd_spi.max_speed_hz = 20_000_000; _lcd_spi.mode = 0

        def _lc(c, *d):
            _lcd_dc.set_value(0); _lcd_spi.writebytes([c])
            if d: _lcd_dc.set_value(1); _lcd_spi.writebytes(list(d))

        # OSOYOO ILI9488 初期化
        # MADCTL MV=1(ランドスケープ)が効かない場合はポートレートモードで転置送信
        # MADCTL=0x08: BGR=1のみ(ポートレート), データをnp.transposeして送信
        _lcd_rst.set_value(0); time.sleep(0.12)
        _lcd_rst.set_value(1); time.sleep(0.12)
        _lc(0x01); time.sleep(0.20)  # Software Reset
        # Positive/Negative Gamma
        _lc(0xE0, 0x00,0x03,0x09,0x08,0x16,0x0A,0x3F,0x78,0x4C,0x09,0x0A,0x08,0x16,0x1A,0x0F)
        _lc(0xE1, 0x00,0x16,0x19,0x03,0x0F,0x05,0x32,0x45,0x46,0x04,0x0E,0x0D,0x35,0x37,0x0F)
        _lc(0xC0, 0x17, 0x15)        # Power Control 1
        _lc(0xC1, 0x41)              # Power Control 2
        _lc(0xC5, 0x00, 0x12, 0x80)  # VCOM Control
        # ポートレートモード(MV=0), 180°回転(MY=1+MX=1), BGR=0(R/B入替)
        _lc(0x36, 0xC0)              # MADCTL: MY=1, MX=1, BGR=0 → 180°回転+R/B入替
        _lc(0x3A, 0x66)              # Pixel Format: 18bpp RGB666
        _lc(0xB0, 0x80)              # Interface Mode Control
        _lc(0xB1, 0xA0)              # Frame Rate Control
        _lc(0xB4, 0x02)              # Display Inversion Control
        _lc(0x20)                    # Display Inversion OFF
        _lc(0xB6, 0x02, 0x02)        # Display Function Control
        _lc(0xE9, 0x00)              # Set Image Function
        _lc(0xF7, 0xA9, 0x51, 0x2C, 0x82)  # Adjust Control 3
        _lc(0x11); time.sleep(0.12)  # Sleep Out
        _lc(0x29)                    # Display ON
        _lc(0x38)                    # Idle Mode OFF
        _lc(0x13)                    # Normal Display Mode ON

        def _lcd_worker():
            while True:
                img = _lcd_queue.get()
                if img is None:
                    break
                try:
                    s = img  # 既に_LCD_W×_LCD_H BGRに合成済み
                    # ランドスケープ(480×320)→ポートレート(320×480)転置
                    d = np.ascontiguousarray(np.transpose(s, (1, 0, 2))).tobytes()
                    _lc(0x2A, 0, 0, (_LCD_H-1)>>8, (_LCD_H-1)&0xFF)  # CASET 0-319
                    _lc(0x2B, 0, 0, (_LCD_W-1)>>8, (_LCD_W-1)&0xFF)  # RASET 0-479
                    _lcd_dc.set_value(0); _lcd_spi.writebytes([0x2C]); _lcd_dc.set_value(1)
                    for i in range(0, len(d), 4096):
                        _lcd_spi.writebytes2(d[i:i+4096])
                except Exception as _ex:
                    print(f"[LCD] フレーム送信エラー: {_ex}", flush=True)

        _lcd_queue = queue.Queue(maxsize=1)
        threading.Thread(target=_lcd_worker, daemon=True).start()
        print(f"SPI LCD: ILI9488 {_LCD_W}x{_LCD_H} 18bpp 20MHz 初期化完了")
    except Exception as _lcd_e:
        _lcd_spi = None
        print(f"SPI LCD: 初期化失敗 ({_lcd_e})")

# タッチパネル (XPT2046 ハードウェアSPI bus0 dev1) 初期化
if _LCD_HW_OK:
    try:
        _tgc = _gpiod.Chip('gpiochip0')
        _touch_irq_line = _tgc.get_line(_TOUCH_IRQ)
        _touch_irq_line.request(consumer='touch', type=_gpiod.LINE_REQ_DIR_IN)
        _touch_spi_dev = _spidev.SpiDev(); _touch_spi_dev.open(0, 1)
        _touch_spi_dev.max_speed_hz = 2_000_000; _touch_spi_dev.mode = 0
        threading.Thread(target=_touch_loop, daemon=True).start()
        _touch_ok = True
        print("タッチパネル: XPT2046 (HW SPI) 初期化完了")
    except Exception as _te:
        print(f"タッチパネル: 初期化失敗 ({_te})")

# カメラ起動（リソース解放待ちでリトライ）
for retry in range(5):
    try:
        picam2.start()
        break
    except RuntimeError as e:
        print(f"カメラ起動失敗 ({retry+1}/5): {e}")
        time.sleep(3)
else:
    print("カメラ起動に失敗しました。終了します。")
    sys.exit(1)

# センサーサイズを取得（ScalerCrop計算に使用）
_sensor_size = picam2.camera_properties.get('PixelArraySize', (im_width, im_height))
SENSOR_W, SENSOR_H = _sensor_size[0], _sensor_size[1]
print(f"センサーサイズ(物理): {SENSOR_W}x{SENSOR_H}")

# 起動直後のデフォルトScalerCropを保存（=カメラが自動選択した全画素設定）
# PixelArraySizeは光学全画素(4056x3040等)だが、デフォルトScalerCropは
# 現在のビニングモード(2028x1520等)に合わせた値のため、それを使うのが正しい
time.sleep(0.5)  # カメラが安定してからメタデータを取得
try:
    _meta = picam2.capture_metadata()
    DEFAULT_SCALER_CROP = _meta.get('ScalerCrop', None)
    print(f"デフォルトScalerCrop: {DEFAULT_SCALER_CROP}")
except Exception:
    DEFAULT_SCALER_CROP = None

_crop_verify_counter = 0
current_inference_roi = None  # None=フルセンサー（座標変換不要）

def apply_scaler_crop(mode):
    """ScalerCropを切り替える（カメラ再起動不要）。
    ScalerCrop: 出力解像度のアスペクト比に合わせて歪みなし表示。
    推論ROI: 常に正方形（センサー高さ基準、センサー中央）。
    """
    global _crop_verify_counter, current_inference_roi
    try:
        if DEFAULT_SCALER_CROP:
            sc_x, sc_y, sc_w, sc_h = DEFAULT_SCALER_CROP
        else:
            sc_x, sc_y, sc_w, sc_h = 0, 0, SENSOR_W, SENSOR_H

        controls = {}

        try:
            val = int(mode)
        except (ValueError, TypeError):
            val = 100

        pct = max(1, min(100, val))
        # 100% = SENSOR_H を基準にクロップ幅計算（最大はsc_wを超えない）
        cw = min(int(SENSOR_H * pct / 100), sc_w)
        cx = sc_x + (sc_w - cw) // 2
        effective_pct = int(cw * 100 / SENSOR_H)

        # ScalerCrop: 出力アスペクト比に合わせて歪みなし（正方形出力はそのまま正方形）
        if disp_w != disp_h:
            ch = min(int(cw * disp_h / disp_w), sc_h)
        else:
            ch = cw
        crop_cy = sc_y + (sc_h - ch) // 2
        crop_cy = max(sc_y, min(crop_cy, sc_y + sc_h - ch))

        # 推論ROIは常に正方形（ScalerCrop中心Yと同じ中心に配置）
        # FHD等16:9では cw > sc_h になるため、ScalerCropのY中心に合わせてROI_cyを計算する
        roi_cy = crop_cy + (ch - cw) // 2
        roi_cy = max(0, min(roi_cy, SENSOR_H - cw))
        controls["ScalerCrop"] = (cx, crop_cy, cw, ch)

        if RECOGNITION_ENABLED:
            controls["FrameDurationLimits"] = (66666, 66666)
            imx500.set_inference_roi_abs((cx, roi_cy, cw, cw))
            current_inference_roi = (cx, roi_cy, cw, cw)
        print(f"[CROP] {CAMERA_RESOLUTION} {effective_pct}%: ScalerCrop=({cx},{crop_cy},{cw},{ch}), ROI=({cx},{roi_cy},{cw},{cw})", flush=True)

        if controls:
            picam2.set_controls(controls)
        _crop_verify_counter = 10
    except Exception as e:
        print(f"[CROP] ScalerCrop設定エラー: {e}", flush=True)

# 起動時クロップ設定（全モード apply_scaler_crop で統一）
apply_scaler_crop(RESOLUTION_SETTING)

# グローバル変数
fps_timestamps = deque()  # 直近1秒のフレームタイムスタンプ（1秒平均FPS用）
GRID_SIZE = 50
last_web_update_time = 0
last_hdmi_update_time = 0
last_detections = []
frame_count = 0
last_np_outputs_none_count = 0
inference_ok_count = 0
inference_none_count = 0
inference_history = deque(maxlen=100)  # True=推論OK, False=None
inference_ok_in_window = 0  # inference_history内のTrue数（O(1)更新）

CONFIG_CHECK_INTERVAL = 1.0  # 1秒ごとに設定変更をチェック（キャッシュ参照のみでI/Oなし）
last_recognition_check_time = 0
last_resolution_check_time = 0

model_status_set = False

if RECOGNITION_ENABLED:
    print("IMX500 YOLO11nで認識を開始します...")
    THRESHOLD = load_threshold_config()
    print(f"[THRESHOLD] 初期しきい値: {THRESHOLD}")
else:
    print(f"認識OFFモード: カメラキャプチャのみ ({im_width}x{im_height})")

# --- キャプチャスレッド: 推論待ちと描画処理を並行実行 ---
capture_frame_queue = queue.Queue(maxsize=1)
capture_stop_event = threading.Event()

def capture_loop():
    while not capture_stop_event.is_set():
        try:
            request = picam2.capture_request()
            frame_main = request.make_array("main")
            if _use_lores:
                _yuv = request.make_array("lores")
                # YUV420 (I420) → BGR
                frame_disp = cv2.cvtColor(_yuv, cv2.COLOR_YUV2BGR_I420)
            else:
                frame_disp = frame_main
            # LCD用に事前ダウンスケール（キャプチャスレッドで並列処理）
            if _lcd_queue is not None:
                frame_lcd_raw = np.zeros((_LCD_H, _LCD_W, 3), dtype=np.uint8)
                frame_lcd_raw[_LCD_OFF_Y:_LCD_OFF_Y+_LCD_NEW_H, _LCD_OFF_X:_LCD_OFF_X+_LCD_NEW_W] = \
                    cv2.resize(frame_disp, (_LCD_NEW_W, _LCD_NEW_H), interpolation=cv2.INTER_LINEAR)
            else:
                frame_lcd_raw = None
            metadata = request.get_metadata() if RECOGNITION_ENABLED else None
            try:
                capture_frame_queue.put((frame_disp, frame_main, frame_lcd_raw, metadata, request), timeout=1.0)
            except queue.Full:
                request.release()
        except Exception as e:
            if not capture_stop_event.is_set():
                print(f"Capture thread error: {e}")
                time.sleep(0.01)

if RECOGNITION_ENABLED:
    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()
    print("キャプチャスレッド起動")
else:
    capture_thread = None
    print("認識OFFモード: キャプチャスレッドなし（SaveWorkerが1秒ごとに取得）")


def stop_capture_then_restart():
    """キャプチャスレッドを安全に停止してからpicam2を停止し再起動する。
    capture_stop_eventを先にセットしないとDMAバッファを保持したままpicam2.stop()が
    呼ばれてV4L2タイムアウトが発生するため、この関数を経由して再起動すること。
    認識OFFモードではSaveWorkerのcapture_arrayが完了するまでcamera_reconfig_eventで待機する。"""
    if RECOGNITION_ENABLED and capture_thread is not None:
        capture_stop_event.set()
        while not capture_frame_queue.empty():
            try:
                _, _, _, _, req = capture_frame_queue.get_nowait()
                req.release()
            except Exception:
                break
        capture_thread.join(timeout=2.0)
    else:
        # 認識OFFモード: SaveWorkerのcapture_array()が完了するまで待機してからstop
        camera_reconfig_event.set()
        time.sleep(0.3)
    picam2.stop()
    sys.exit(1)




def switch_recognition_off():
    """認識ON → OFFのソフトスイッチ（カメラ再起動なし）。
    キャプチャスレッドを停止し RECOGNITION_ENABLED を False にするだけ。
    カメラは継続稼動し、SaveWorkerが capture_array() で1秒ごとに画像取得する。
    認識OFF → ONはIMX500ファームウェア転送が必要なため完全再起動（stop_capture_then_restart）。"""
    global RECOGNITION_ENABLED
    print("[MODE] 認識ON→OFF 切り替え開始")
    try:
        # 1. キャプチャスレッド停止（カメラは継続稼動）
        capture_stop_event.set()
        while not capture_frame_queue.empty():
            try:
                _, _, _, _, req = capture_frame_queue.get_nowait()
                req.release()
            except Exception:
                break
        if capture_thread is not None:
            capture_thread.join(timeout=3.0)

        # 2. グローバル・キャッシュ更新
        RECOGNITION_ENABLED = False
        config_cache['recognition_enabled'] = False
        worker.last_capture_time = 0.0

        # 3. 検出枠オーバーレイをクリア（透明にリセット）
        annotation_bgra[:] = 0
        if HDMI_ENABLED:
            try:
                picam2.set_overlay(None)
            except Exception as e:
                print(f"[MODE] HDMIオーバーレイクリアエラー: {e}")

        # 4. ステータスを「準備完了」に更新してUIのオーバーレイを解除
        set_model_status_ready()
        print("[MODE] 認識ON→OFF完了（カメラ継続稼動・SaveWorkerで画像取得）")
    except Exception as e:
        print(f"[MODE] 認識OFF切り替えエラー: {e}、再起動します")
        stop_capture_then_restart()


# 設定キャッシュ（SaveWorkerがバックグラウンドで更新、メインスレッドはキャッシュを参照）
config_cache = {
    'recognition_enabled': RECOGNITION_ENABLED,
    'resolution': RESOLUTION_SETTING,
    'model_changed': False,
    'threshold': THRESHOLD,
    'save_config': load_save_config(),
    'disk_space_ok': True,
    'cpu_temp': 0.0,
}
save_queue = queue.Queue(maxsize=3)
worker = SaveWorker(save_queue, config_cache)
worker.start()

# 認識OFFモードはメインループがフレーム処理をスキップするため、ここでステータスを設定
if not RECOGNITION_ENABLED:
    set_model_status_ready()
    model_status_set = True

avg_fps = 0.0
hdmi_interval = 1 / 15

# 表示解像度ベースでバッファ・スケールを設定（推論はim_width/im_heightのまま）
_is_fhd = (disp_w >= 1920)
# FHDはオーバーレイを半解像度(960×540)で描画してリサイズステップを省略
ov_w = disp_w // 2 if _is_fhd else disp_w
ov_h = disp_h // 2 if _is_fhd else disp_h
TXT_SCALE = 0.5 if (disp_w == 480) else (1.0 if (disp_w == 640 and disp_h == 640) else (1.0 if _is_fhd else 3.0))
LINE_THICK = 1 if (disp_w == 480) else (2 if (disp_w == 640 and disp_h == 640) else (2 if _is_fhd else 8))
# テキストY座標（TXT_SCALEに比例してスケール）
TXT_Y1 = round(50  * TXT_SCALE / 1.6)
TXT_Y2 = round(100 * TXT_SCALE / 1.6)
annotation_bgra = np.zeros((ov_h, ov_w, 4), dtype=np.uint8)
hdmi_rgba = np.empty((ov_h, ov_w, 4), dtype=np.uint8)

# main → カメラ表示解像度へのbboxスケール係数（推論・中心座標計算はカメラ解像度で実施）
_bbox_xs = disp_w / im_width
_bbox_ys = disp_h / im_height
_ov_s = ov_w / disp_w  # カメラ解像度 → オーバーレイへのスケール（FHDは0.5）

try:
    while True:
        now = time.time()
        frame_count += 1

        # 解像度変更を素早く反映（0.2秒間隔でキャッシュ参照）
        if now - last_resolution_check_time >= 0.2:
            new_resolution = config_cache['resolution']
            if new_resolution != RESOLUTION_SETTING:
                RESOLUTION_SETTING = new_resolution
                apply_scaler_crop(RESOLUTION_SETTING)
            last_resolution_check_time = now

        if now - last_recognition_check_time >= CONFIG_CHECK_INTERVAL:
            # ファイルI/OはSaveWorkerがバックグラウンドで実施済み、キャッシュを参照するだけ
            new_enabled = config_cache['recognition_enabled']
            if new_enabled != RECOGNITION_ENABLED:
                if not new_enabled:
                    # ON → OFF: カメラ継続稼動のまま認識OFF（再起動なし）
                    switch_recognition_off()
                else:
                    # OFF → ON: IMX500ファームウェア転送が必要なため完全再起動
                    print("認識ON切り替え: 完全再起動します（IMX500ファームウェア転送が必要）")
                    stop_capture_then_restart()

            new_cam_res = config_cache.get('camera_resolution', CAMERA_RESOLUTION)
            if new_cam_res != CAMERA_RESOLUTION:
                print(f"解像度設定が変更されました ({CAMERA_RESOLUTION} → {new_cam_res})。再起動します...")
                stop_capture_then_restart()

            if RECOGNITION_ENABLED:
                if config_cache['model_changed']:
                    print("モデル設定が変更されました。再起動します...")
                    stop_capture_then_restart()
                new_threshold = config_cache['threshold']
                if abs(new_threshold - THRESHOLD) > 0.0001:
                    old_threshold = THRESHOLD
                    THRESHOLD = new_threshold
                    print(f"[THRESHOLD] しきい値を更新: {old_threshold:.3f} → {THRESHOLD:.3f}")

            last_recognition_check_time = now

        if not RECOGNITION_ENABLED:
            time.sleep(0.1)
            continue

        frame, frame_main, frame_lcd_raw, metadata, capture_request_obj = capture_frame_queue.get()

        if RECOGNITION_ENABLED and metadata is not None:
            # ScalerCrop検証（apply_scaler_crop呼出後10フレーム以内）
            if _crop_verify_counter > 0:
                _crop_verify_counter -= 1
                if _crop_verify_counter == 0:
                    actual_crop = metadata.get('ScalerCrop', 'N/A')
                    print(f"[CROP VERIFY] メタデータのScalerCrop={actual_crop}"
                          f" 解像度設定={RESOLUTION_SETTING}", flush=True)
            detections = parse_detections(metadata)
            labels = get_labels()

            # 物体認識とシグナルタワーを分離
            signal_detections = []
            object_detections = []
            for det in detections:
                category = int(det.category)
                if category < len(labels):
                    label_lower = labels[category].lower()
                    is_signal = any(label_lower.startswith(p) for p in SIGNAL_COLOR_PREFIXES)
                    if is_signal:
                        signal_detections.append(det)
                    else:
                        object_detections.append(det)
                else:
                    object_detections.append(det)

            # 物体認識・シグナルタワーともに重なり可（NMSなし）
            detections = object_detections + signal_detections
        else:
            detections = []

        detected = False
        label_list = []
        object_log_data = []
        st_log_data = []
        st_detected_keys = set()  # 今フレームで検出されたシグナルタワーキー（毎フレーム更新用）
        st_frame_states = {}      # 今フレームの (region_id, color) → 状態（変化ログ用）
        current_detection_centers = []  # ID設定用：今フレームの検出中心リスト

        if RECOGNITION_ENABLED:
            # 推論状況を300フレームごと（≒20秒ごと）に出力。
            # 高頻度(10フレームごと)にするとstdout→journald→SDカード書き込みが
            # 逼迫し、web_server側のログ出力ブロックによる画像停止を誘発するため削減。
            if frame_count % 300 == 0:
                total = inference_ok_count + inference_none_count
                rate = (inference_ok_count / total * 100) if total > 0 else 0
                print(f"[INFERENCE] フレーム{frame_count}: 推論OK={inference_ok_count} None={inference_none_count} 成功率={rate:.0f}% FPS={avg_fps:.1f}", flush=True)

            # 定期的なステータス出力（100フレームごとに削減）
            if frame_count % 100 == 0:
                if len(detections) == 0:
                    print(f"フレーム {frame_count}: 検出数=0, しきい値={THRESHOLD:.3f}, None回数={last_np_outputs_none_count}")
                else:
                    labels = get_labels()
                    classes_str = ", ".join([f"{labels[int(d.category)]}({d.conf:.2f})" for d in detections[:3] if int(d.category) < len(labels)])
                    print(f"フレーム {frame_count}: 検出数={len(detections)}, しきい値={THRESHOLD:.3f}, クラス=[{classes_str}]")

            labels = get_labels()

        # FPS計算（ダーティチェック前に実行）
        now = time.time()
        fps_timestamps.append(now)
        while fps_timestamps and fps_timestamps[0] < now - 1.0:
            fps_timestamps.popleft()
        avg_fps = len(fps_timestamps)

        # CPU温度はSaveWorkerがバックグラウンドで更新（sysfs I/Oなし）
        cpu_temp = config_cache['cpu_temp']

        annotation_bgra[:] = 0

        # シグナルタワー: 同一ID枠内で同じ色が複数検出された場合、スコア最高のものだけ残す
        if MODEL_TYPE == 'signal_tower' and signal_regions and RECOGNITION_ENABLED:
            _best_signal = {}  # (region_id, color_prefix) -> (score, detection_object)
            for _det in detections:
                _cat = int(_det.category)
                if _cat < 0 or _cat >= len(labels):
                    continue
                _lbl = labels[_cat].lower()
                for _prefix in SIGNAL_COLOR_PREFIXES:
                    if _lbl.startswith(_prefix):
                        _bx, _by, _bw, _bh = _det.box
                        _ls = int(_bx * _bbox_xs)
                        _rs = int((_bx + _bw) * _bbox_xs)
                        _ts = int(_by * _bbox_ys)
                        _bs = int((_by + _bh) * _bbox_ys)
                        _cx = (_ls + _rs) // 2
                        _cy = (_ts + _bs) // 2
                        _rid = find_signal_region_id(_cx, _cy)
                        if _rid != 0:
                            _key = (_rid, _prefix)
                            if _key not in _best_signal or _det.conf > _best_signal[_key][0]:
                                _best_signal[_key] = (_det.conf, _det)
                        break
            _best_det_ids = {id(v[1]) for v in _best_signal.values()}
        else:
            _best_det_ids = None

        for detection in detections:
            try:
                x, y, w, h = detection.box
                category = int(detection.category)
                score = detection.conf

                if category < 0 or category >= len(labels):
                    continue

                label = labels[category]
                label_list.append(label)

                # main解像度 → 表示解像度へ座標変換
                ls = int(x * _bbox_xs)
                ts = int(y * _bbox_ys)
                rs = int((x + w) * _bbox_xs)
                bs = int((y + h) * _bbox_ys)
                left, top, right, bottom = ls, ts, rs, bs

                # 画面外またはわずかにかかるだけの検出枠はスキップ（カメラ解像度で判定）
                visible_top = max(ts, 0)
                visible_bottom = min(bs, disp_h)
                visible_left = max(ls, 0)
                visible_right = min(rs, disp_w)
                if visible_bottom - visible_top < disp_w * 0.01 or visible_right - visible_left < disp_w * 0.01:
                    continue

                # オーバーレイ描画用座標（FHDは0.5倍に縮小）
                ols = int(ls * _ov_s)
                ots = int(ts * _ov_s)
                ors = int(rs * _ov_s)
                obs = int(bs * _ov_s)

                # シグナルタワーかチェック（物体名が色名で始まる）
                label_lower = label.lower()
                is_signal_tower = any(label_lower.startswith(prefix) for prefix in SIGNAL_COLOR_PREFIXES)

                if is_signal_tower:
                    # ID枠内で同色の重複検出は最高スコアのみ採用
                    if _best_det_ids is not None and id(detection) not in _best_det_ids:
                        continue
                    # シグナルタワー: 物体名の色で枠とスコア表示
                    display_label = f"{score:.2f}"
                    box_color = (*get_label_color(label), 255)
                    cv2.rectangle(annotation_bgra, (ols, ots), (ors, obs), box_color, LINE_THICK)
                    mid_y = (ots + obs) // 2
                    # スコアを枠の右外に表示
                    (dlw, dlh), _ = cv2.getTextSize(display_label, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
                    cv2.putText(annotation_bgra, display_label, (ors + 4, mid_y + dlh // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, box_color, LINE_THICK)

                    # 毎フレームst_color_historyを更新（Trueを追記）
                    center_x = int((left + right) / 2)
                    center_y = int((top + bottom) / 2)
                    gx = center_x // GRID_SIZE
                    gy = center_y // GRID_SIZE
                    current_detection_centers.append((center_x, center_y, int(h), rs - ls))
                    for prefix in SIGNAL_COLOR_PREFIXES:
                        if label_lower.startswith(prefix):
                            key = (gx, gy, prefix)
                            if key not in st_color_history:
                                st_color_history[key] = deque(maxlen=45)
                            elif now - st_last_detected_time.get(key, 0) > 1.0:
                                # 1秒以上未検出後の再検出 → 点灯開始とみなしヒストリをリセット
                                st_color_history[key] = deque(maxlen=45)
                                st_blink_start_time.pop(key, None)
                            st_color_history[key].append(True)
                            st_last_detected_time[key] = now
                            st_detected_keys.add(key)
                            break

                    # 点灯/点滅状態を判定して枠の左外に表示（3秒分バッファが満杯になってから判定）
                    st_state = "ON"
                    for prefix in SIGNAL_COLOR_PREFIXES:
                        if label_lower.startswith(prefix):
                            _bkey = (gx, gy, prefix)
                            hist = st_color_history.get(_bkey, deque())
                            if len(hist) >= 45:
                                false_count = sum(1 for v in hist if not v)
                                if false_count > len(hist) * 0.2:
                                    st_state = "BLINK"
                            break
                    (sw, sh), _ = cv2.getTextSize(st_state, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
                    cv2.putText(annotation_bgra, st_state, (ols - sw - 4, mid_y + sh // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, box_color, LINE_THICK)

                    # ファイル名用にラベルへ状態を付加（例: red_ON, yellow_BLINK）
                    if label_list:
                        label_list[-1] = f"{label}_{st_state}"

                    # シグナルタワーのログデータを収集（region_id付き）
                    region_id = find_signal_region_id(center_x, center_y)
                    for prefix in SIGNAL_COLOR_PREFIXES:
                        if label_lower.startswith(prefix):
                            st_log_data.append((center_x, center_y, prefix, region_id))
                            if region_id != 0:
                                st_frame_states[(region_id, prefix)] = st_state.lower()
                                st_change_last_seen[(region_id, prefix)] = now
                            break

                else:
                    # 通常の物体検出: 物体名から連想される色で表示
                    display_label = f"{label} {score:.2f}"
                    box_color = (*get_label_color(label), 255)
                    cv2.rectangle(annotation_bgra, (ols, ots), (ors, obs), box_color, LINE_THICK)
                    cv2.putText(annotation_bgra, display_label, (ols + 2, obs - 3),
                                cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, box_color, LINE_THICK)

                    # 物体認識のログデータを収集
                    object_log_data.append((label, x, y, w, h, score))
                detected = True
            except Exception as e:
                print(f"検出結果描画エラー: {e}")
                continue

        # 変化ログ: シグナルタワー（ID枠内の点灯/点滅/消灯変化を記録）
        if MODEL_TYPE == 'signal_tower' and RECOGNITION_ENABLED:
            for key, new_state in st_frame_states.items():
                old_state = st_change_prev_state.get(key, 'off')
                if old_state != new_state:
                    log_signal_change(key[0], key[1], old_state, new_state)
                st_change_prev_state[key] = new_state
            for key, prev_state in list(st_change_prev_state.items()):
                if prev_state != 'off' and key not in st_frame_states:
                    if now - st_change_last_seen.get(key, 0) > ST_OFF_DEBOUNCE:
                        log_signal_change(key[0], key[1], prev_state, 'off')
                        st_change_prev_state[key] = 'off'

            # 送信用ログ: 名前付きIDの状態コードを10分ごとファイルに記録（変化時のみ追記）
            send_logger.update(now, st_change_prev_state)

        # 変化ログ: 物体認識（検出開始/消失を記録）
        if MODEL_TYPE == 'object_detection' and RECOGNITION_ENABLED:
            current_labels = set(item[0] for item in object_log_data)
            for label in current_labels:
                obj_change_last_seen[label] = now
                if obj_change_logged_state.get(label) != 'detected':
                    log_detection_change(label, 'detected')
                    obj_change_logged_state[label] = 'detected'
            for label, state in list(obj_change_logged_state.items()):
                if state == 'detected' and label not in current_labels:
                    if now - obj_change_last_seen.get(label, 0) > OBJ_LOST_DEBOUNCE:
                        log_detection_change(label, 'lost')
                        obj_change_logged_state[label] = 'lost'

        # IDリセットトリガーチェック
        if MODEL_TYPE == 'signal_tower' and os.path.exists(SIGNAL_ID_RESET_PATH):
            os.remove(SIGNAL_ID_RESET_PATH)
            signal_regions = []
            try:
                if os.path.exists(SIGNAL_REGIONS_PATH):
                    os.remove(SIGNAL_REGIONS_PATH)
            except Exception as e:
                print(f"[SIGNAL] 領域リセットエラー: {e}")
            print("[SIGNAL] ID領域をリセットしました")

        # ID設定: トリガーで3秒収集開始、3秒後に確定
        if MODEL_TYPE == 'signal_tower':
            if os.path.exists(SIGNAL_ID_TRIGGER_PATH):
                os.remove(SIGNAL_ID_TRIGGER_PATH)
                _id_collecting = True
                _id_collect_start = now
                _id_collect_centers = []
                print("[SIGNAL] ID設定: 3秒間の収集開始")
            if _id_collecting:
                _id_collect_centers.extend(current_detection_centers)
                if now - _id_collect_start >= 3.0:
                    _id_collecting = False
                    if _id_collect_centers:
                        # X距離が検出枠幅以内かつY距離が検出枠高さの5倍以内のものを同一クラスタにまとめる
                        raw = list(set(_id_collect_centers))  # (cx, cy, bh, bw)
                        clusters = []
                        used = [False] * len(raw)
                        for i, p in enumerate(raw):
                            if used[i]:
                                continue
                            group = [p]
                            used[i] = True
                            for j, q in enumerate(raw):
                                if not used[j]:
                                    avg_h = (p[2] + q[2]) / 2
                                    avg_w = (p[3] + q[3]) / 2
                                    if abs(p[1] - q[1]) <= avg_h * 5 and abs(p[0] - q[0]) <= avg_w:
                                        group.append(q)
                                        used[j] = True
                            clusters.append(group)
                        # 各クラスタの中心X・平均高さ・Y範囲を計算してX順にソート
                        tower_data = sorted(
                            [
                                (
                                    int(sum(pt[0] for pt in g) / len(g)),
                                    int(sum(pt[2] for pt in g) / len(g)),
                                    min(pt[1] for pt in g),
                                    max(pt[1] for pt in g),
                                )
                                for g in clusters
                            ],
                            key=lambda t: t[0]
                        )
                        # X半幅: 異なるX中心間の最小距離の半分（1本の場合は画幅の1/4）
                        distinct_xs = sorted(set(t[0] for t in tower_data))
                        if len(distinct_xs) >= 2:
                            x_half_width = int(min(
                                distinct_xs[k+1] - distinct_xs[k]
                                for k in range(len(distinct_xs) - 1)
                            ) / 2)
                        else:
                            x_half_width = ov_w // 4
                        # 各クラスタを中心とする同サイズ・非重複最大矩形にID枠を設定
                        # 各ペアをdx>=dyならX方向、dy>dxならY方向で分離し、共通セルサイズを決定
                        n_td = len(tower_data)
                        cxs_td = [t[0] for t in tower_data]
                        cys_td = [(t[2] + t[3]) // 2 for t in tower_data]
                        W_cons, H_cons = [], []
                        for i in range(n_td):
                            for j in range(i + 1, n_td):
                                dx = abs(cxs_td[i] - cxs_td[j])
                                dy = abs(cys_td[i] - cys_td[j])
                                if dx >= dy:
                                    W_cons.append(dx)
                                else:
                                    H_cons.append(dy)
                        cell_xhw = (min(W_cons) if W_cons else disp_w) // 2
                        cell_yhalf = (min(H_cons) if H_cons else disp_h) // 2
                        signal_regions = []
                        for i, (cx, avg_h, y_min, y_max) in enumerate(tower_data):
                            cy = cys_td[i]
                            signal_regions.append({
                                'id': i + 1,
                                'cx': cx,
                                'x_half_width': cell_xhw,
                                'y_top': max(0, cy - cell_yhalf),
                                'y_bottom': min(disp_h, cy + cell_yhalf),
                            })
                        save_signal_regions_file(signal_regions)
                        print(f"[SIGNAL] ID設定: {len(signal_regions)}領域, X半幅={x_half_width}px")
                    else:
                        print("[SIGNAL] ID設定: 収集期間中に検出なし")

        # シグナルタワーID領域を描画（縦長の長方形）
        if MODEL_TYPE == 'signal_tower' and signal_regions:
            for region in signal_regions:
                if 'x_half_width' in region:
                    rx1 = max(0, int((region['cx'] - region['x_half_width']) * _ov_s))
                    rx2 = min(ov_w, int((region['cx'] + region['x_half_width']) * _ov_s))
                    ry1 = int(region.get('y_top', 0) * _ov_s)
                    ry2 = min(ov_h, int(region.get('y_bottom', disp_h) * _ov_s))
                    cv2.rectangle(annotation_bgra, (rx1, ry1), (rx2, ry2), ID_REGION_COLOR, LINE_THICK)
                    id_text = f"ID:{region['id']}"
                    (tw, th), _ = cv2.getTextSize(id_text, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
                    cv2.putText(annotation_bgra, id_text, (rx1 + 4, ry1 + th + 4),
                                cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, ID_REGION_COLOR, LINE_THICK)
                else:
                    # 旧形式（円）の後方互換
                    rcx = int(region['cx'] * _ov_s)
                    rcy = int(region['cy'] * _ov_s)
                    rrad = int(region['radius'] * _ov_s)
                    cv2.circle(annotation_bgra, (rcx, rcy), rrad, ID_REGION_COLOR, LINE_THICK)
                    id_text = f"ID:{region['id']}"
                    (tw, th), _ = cv2.getTextSize(id_text, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
                    cv2.putText(annotation_bgra, id_text,
                                (rcx - rrad + 4, rcy - rrad + th + 4),
                                cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, ID_REGION_COLOR, LINE_THICK)

        # FPS表示
        cv2.putText(annotation_bgra, f"FPS: {avg_fps}", (5, TXT_Y1),
                    cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, (255, 255, 255, 255), LINE_THICK)

        # 推論成功率を表示（過去100回）
        if RECOGNITION_ENABLED and len(inference_history) > 0:
            inf_rate = inference_ok_in_window / len(inference_history) * 100
            inf_color = (0, 255, 0, 255) if inf_rate >= 95 else (0, 255, 255, 255) if inf_rate >= 80 else (0, 0, 255, 255)
            cv2.putText(annotation_bgra, f"INF: {inf_rate:.0f}%", (5, TXT_Y2),
                        cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, inf_color, LINE_THICK)

        # CPU温度を右上に表示
        temp_text = f"{cpu_temp:.1f}C"
        temp_color = (0, 255, 0, 255) if cpu_temp < 70 else (0, 255, 255, 255) if cpu_temp < 80 else (0, 0, 255, 255)
        (tw, th), _ = cv2.getTextSize(temp_text, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
        cv2.putText(annotation_bgra, temp_text, (ov_w - tw - 5, TXT_Y1),
                    cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, temp_color, LINE_THICK)

        # クロップ設定を左下に表示
        try:
            _val = int(RESOLUTION_SETTING)
            crop_text = '100%' if _val >= 100 else f'{_val}%'
        except (ValueError, TypeError):
            crop_text = '100%'
        cv2.putText(annotation_bgra, crop_text, (5, ov_h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, (0, 255, 255, 255), LINE_THICK)

        # 日時を右下に表示
        datetime_text = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        (dtw, dth), _ = cv2.getTextSize(datetime_text, cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, LINE_THICK)
        cv2.putText(annotation_bgra, datetime_text, (ov_w - dtw - 5, ov_h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, TXT_SCALE, (255, 255, 255, 255), LINE_THICK)

        if RECOGNITION_ENABLED:
            # st_color_history: 未検出キーにFalseを毎フレーム追記
            for key in list(st_color_history.keys()):
                if key not in st_detected_keys:
                    st_color_history[key].append(False)
            if len(st_color_history) > 100:
                keys_to_remove = list(st_color_history.keys())[:-100]
                for key in keys_to_remove:
                    del st_color_history[key]
                    st_blink_start_time.pop(key, None)
                    st_last_detected_time.pop(key, None)

        # 最初のフレーム保存後にモデル準備完了を通知
        if not model_status_set:
            set_model_status_ready()
            model_status_set = True

        t_hdmi_now = time.time()
        hdmi_due = HDMI_ENABLED and RECOGNITION_ENABLED and t_hdmi_now - last_hdmi_update_time >= hdmi_interval
        if hdmi_due:
            last_hdmi_update_time = t_hdmi_now
            cv2.cvtColor(annotation_bgra, cv2.COLOR_BGRA2RGBA, dst=hdmi_rgba)

        now = time.time()
        if now - last_web_update_time >= 1.0:
            frame_copy = frame.copy()
            ann_snap = annotation_bgra.copy()
            try:
                save_queue.put_nowait((frame_copy, ann_snap, list(label_list), list(st_log_data), list(object_log_data)))
                last_web_update_time = now
            except queue.Full:
                if frame_count % 50 == 0:
                    print(f"[WEB] save_queue full (qsize={save_queue.qsize()}), skipping web update")

        capture_request_obj.release()

        # DMA解放後にset_overlay
        if hdmi_due:
            try:
                picam2.set_overlay(hdmi_rgba)
            except Exception:
                pass

        if _lcd_queue is not None and frame_lcd_raw is not None:
            try:
                if _shutdown_in_progress:
                    # シャットダウン中は真っ黒の画面に。15秒間は「SHUTTING DOWN...」を
                    # 中央に表示し、15秒経過後は文字なしの完全な黒にする（Web版と同様）。
                    # ※バックライトは電源直結のため消灯不可。ピクセルのみ黒にする。
                    _lcd_frame = np.zeros_like(frame_lcd_raw)
                    if time.time() - _shutdown_started_at < 15.0:
                        _sd_msg = "SHUTTING DOWN..."
                        (_sdw, _sdh), _ = cv2.getTextSize(_sd_msg, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                        cv2.putText(_lcd_frame, _sd_msg, ((_LCD_W - _sdw) // 2, (_LCD_H + _sdh) // 2),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                else:
                    # キャプチャスレッドで事前ダウンスケール済み frame_lcd_raw にアノテーションを合成
                    _lcd_frame = frame_lcd_raw.copy()
                    _ann_s = cv2.resize(annotation_bgra, (_LCD_NEW_W, _LCD_NEW_H), interpolation=cv2.INTER_NEAREST)
                    _roi = _lcd_frame[_LCD_OFF_Y:_LCD_OFF_Y+_LCD_NEW_H, _LCD_OFF_X:_LCD_OFF_X+_LCD_NEW_W]
                    _roi[_ann_s[:,:,3] > 0] = _ann_s[:,:,:3][_ann_s[:,:,3] > 0]
                    if _touch_ok:
                        draw_touch_buttons(_lcd_frame)
                _lcd_queue.put_nowait(_lcd_frame)
            except queue.Full:
                pass


except KeyboardInterrupt:
    print("終了します")

finally:
    capture_stop_event.set()
    if _lcd_queue is not None:
        _lcd_queue.put(None)
    if _lcd_spi is not None:
        _lcd_spi.close()
    picam2.stop()
