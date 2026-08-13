#!/usr/bin/env python3
"""
web_server.py - モデル切り替え機能付きWebサーバー
index.htmlとresult.jpgを配信し、モデル切り替えリクエストを処理する
"""
import os
import json
import re
import hashlib
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor


# --- 遅いリクエストの診断ログ（間欠フリーズの原因特定用） ---
# 注意: stdout/journald経由(=SDカード)へのログはワーカー連鎖停止を招くため使わない。
# tmpfsの/tmpにのみ追記する（サービス再起動では消えず、再起動=リブートでのみ消える）。
SLOW_REQ_SEC = 2.0
SLOW_LOG_PATH = "/tmp/web_slow.log"
_active_lock = threading.Lock()
_active_requests = 0


def _log_slow_request(path, duration, active):
    try:
        line = "%s  %5.1fs  active=%d  %s\n" % (
            time.strftime("%H:%M:%S"), duration, active, path)
        with open(SLOW_LOG_PATH, 'a') as f:
            f.write(line)
    except Exception:
        pass


class PooledHTTPServer(HTTPServer):
    """固定数のワーカースレッドでリクエストを処理するHTTPサーバー。
    無制限スレッド生成によるスレッド枯渇を防ぎつつ、遅いリクエストが
    画像取得を直列ブロックしないようにする。"""
    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass, max_workers=6):
        super().__init__(server_address, RequestHandlerClass)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def process_request(self, request, client_address):
        self._executor.submit(self._process, request, client_address)

    def _process(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def server_close(self):
        try:
            self._executor.shutdown(wait=False)
        finally:
            super().server_close()

# 設定
PORT = 8080
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_IMAGE_PATH = "/tmp/result.jpg"
MODEL_CONFIG_PATH = "/home/pi/model_config.json"  # 永続的な場所に保存（再起動後も保持）
MODEL_STATUS_PATH = "/tmp/model_status.json"
SAVE_CONFIG_PATH = "/home/pi/save_config.json"  # 保存設定を永続化
THRESHOLD_CONFIG_PATH = "/home/pi/threshold_config.json"  # しきい値設定を永続化
RECOGNITION_CONFIG_PATH = "/home/pi/recognition_config.json"  # 認識設定を永続化
ID_NAMES_CONFIG_PATH = "/home/pi/id_names_config.json"  # ID名前設定を永続化
STATUS_CODE_CONFIG_PATH = "/home/pi/status_code_config.json"  # 状態コードのパターン定義を永続化
LOG_DIR = "/home/pi/logs/"            # CSVログ保存ディレクトリ
SEND_LOG_DIR = "/home/pi/send_logs/"  # 送信用ログ保存ディレクトリ
SEND_TARGET_CONFIG_PATH = "/home/pi/send_target_config.json"  # 送信用ログの送信先設定を永続化
SEND_TARGET_STATE_PATH = "/home/pi/send_target_state.json"    # 送信済みファイル名の記録

# モデル設定
MODELS = {
    'object_detection': {
        'network': '/home/pi/object_detection/network.rpk',
        'labels': '/home/pi/object_detection/labels.txt'
    },
    'signal_tower': {
        'network': '/home/pi/signal_tower/network.rpk',
        'labels': '/home/pi/signal_tower/labels.txt'
    }
}

# 現在のモデル（デフォルト: object_detection）
current_model = 'object_detection'


def save_model_config(model_type):
    """モデル設定をファイルに保存"""
    config = {
        'model_type': model_type,
        'network': MODELS[model_type]['network'],
        'labels': MODELS[model_type]['labels']
    }
    with open(MODEL_CONFIG_PATH, 'w') as f:
        json.dump(config, f)
    # ステータスを「切り替え中」に設定
    save_model_status('switching', model_type)
    return config


def save_model_status(status, model_type):
    """モデルステータスをファイルに保存"""
    import time
    status_data = {
        'status': status,
        'model_type': model_type,
        'timestamp': time.time()
    }
    with open(MODEL_STATUS_PATH, 'w') as f:
        json.dump(status_data, f)


def load_model_status():
    """モデルステータスをファイルから読み込み"""
    if os.path.exists(MODEL_STATUS_PATH):
        try:
            with open(MODEL_STATUS_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'status': 'starting', 'model_type': ''}


def load_model_config():
    """モデル設定をファイルから読み込み"""
    global current_model
    if os.path.exists(MODEL_CONFIG_PATH):
        try:
            with open(MODEL_CONFIG_PATH, 'r') as f:
                config = json.load(f)
                current_model = config.get('model_type', 'object_detection')
                return config
        except:
            pass
    return save_model_config(current_model)


def save_save_config(save_result=True, save_raw=True):
    """保存設定をファイルに保存"""
    config = {
        'save_result': save_result,
        'save_raw': save_raw
    }
    with open(SAVE_CONFIG_PATH, 'w') as f:
        json.dump(config, f)
    return config


def load_save_config():
    """保存設定をファイルから読み込み"""
    if os.path.exists(SAVE_CONFIG_PATH):
        try:
            with open(SAVE_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    # デフォルトは両方ON
    return save_save_config(True, True)


def save_threshold_config(threshold=0.25):
    """しきい値設定をファイルに保存"""
    config = {'threshold': threshold}
    with open(THRESHOLD_CONFIG_PATH, 'w') as f:
        json.dump(config, f)
    return config


def load_threshold_config():
    """しきい値設定をファイルから読み込み"""
    if os.path.exists(THRESHOLD_CONFIG_PATH):
        try:
            with open(THRESHOLD_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    # デフォルトは0.25
    return save_threshold_config(0.25)


def save_recognition_config(enabled=True, resolution='full', camera_resolution=None):
    """認識設定をファイルに保存"""
    # 既存のcamera_resolutionを引き継ぐ
    existing = {}
    if os.path.exists(RECOGNITION_CONFIG_PATH):
        try:
            with open(RECOGNITION_CONFIG_PATH, 'r') as f:
                existing = json.load(f)
        except Exception:
            pass
    config = {
        'enabled': enabled,
        'resolution': resolution,
        'camera_resolution': camera_resolution if camera_resolution is not None else existing.get('camera_resolution', '1920x1080'),
    }
    with open(RECOGNITION_CONFIG_PATH, 'w') as f:
        json.dump(config, f)
    return config



def load_recognition_config():
    """認識設定をファイルから読み込み"""
    if os.path.exists(RECOGNITION_CONFIG_PATH):
        try:
            with open(RECOGNITION_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    return save_recognition_config(True, 'full')


def normalize_id_names(id_names):
    """各IDの名前を3項目の文字列リストに正規化する。
    旧形式（単一文字列）も ["旧名", "", ""] に変換して後方互換を保つ。"""
    out = {}
    for k, v in (id_names or {}).items():
        if isinstance(v, list):
            parts = [('' if x is None else str(x)) for x in v][:3]
        else:
            parts = ['' if v is None else str(v)]
        while len(parts) < 3:
            parts.append('')
        out[str(k)] = parts
    return out


def save_id_names_config(id_names):
    """ID名前設定をファイルに保存（3項目リスト形式に正規化）"""
    id_names = normalize_id_names(id_names)
    with open(ID_NAMES_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump({'id_names': id_names}, f, ensure_ascii=False)
    return id_names


def load_id_names_config():
    """ID名前設定をファイルから読み込み"""
    if os.path.exists(ID_NAMES_CONFIG_PATH):
        try:
            with open(ID_NAMES_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return normalize_id_names(data.get('id_names', {}))
        except:
            pass
    return {}


def save_status_code_config(config):
    """状態コードのパターン定義をファイルに保存"""
    patterns = config.get('patterns', [])
    default = config.get('default', 0)
    with open(STATUS_CODE_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump({'patterns': patterns, 'default': default}, f, ensure_ascii=False)
    return {'patterns': patterns, 'default': default}


def load_status_code_config():
    """状態コードのパターン定義をファイルから読み込み"""
    if os.path.exists(STATUS_CODE_CONFIG_PATH):
        try:
            with open(STATUS_CODE_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {'patterns': data.get('patterns', []), 'default': data.get('default', 0)}
        except:
            pass
    return {'patterns': [], 'default': 0}


# --- 送信用ログのMQTT送信（paho-mqtt） ---
# send_logs内のCSVの各行を、Webで設定したMQTTブローカー（IP・ポート・トピック・
# ユーザー名・パスワード）へ送信する。送信形式は child-csv-to-mqtt.py に合わせ、CSVの生1行
# ではなく各行をJSON（event_id/mk_date/sta_no1-3/t1_status/device_id/schema_version）に
# 変換してQoS2でpublishする。event_idは device_id+行内容 のSHA1で決定的に生成するため、
# 同じ行を再送しても受信側で二重記録されない。自動（バックグラウンドで定期）と手動（Web上の
# ボタン）の両方に対応。ファイルごとに送信済み行数を記録し、未送信の行だけを送って二重送信を
# 防ぐ。書き込み途中の不完全な行（末尾が改行で終わらない最終行）は確定するまで送らない。
SEND_TARGET_DEFAULT = {
    'enabled': False,
    'host': '',
    'port': 1883,
    'topic': 'presence/record',
    'username': '',
    'password': '',
    'interval': 600,
    'time_sync_enabled': False,   # 送信先ホストの時刻にPiのシステム時計を同期するか
    'time_sync_interval': 3600,   # 自動同期の間隔（秒）
}

_send_lock = threading.Lock()              # 送信処理の同時実行を防ぐ
_send_status_lock = threading.Lock()
_send_status = {'last_time': '', 'last_result': ''}


def load_send_target_config():
    """送信先設定をファイルから読み込み（欠損項目はデフォルトで補完）"""
    cfg = dict(SEND_TARGET_DEFAULT)
    if os.path.exists(SEND_TARGET_CONFIG_PATH):
        try:
            with open(SEND_TARGET_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    try:
        cfg['port'] = int(cfg.get('port', 1883) or 1883)
    except (ValueError, TypeError):
        cfg['port'] = 1883
    try:
        cfg['interval'] = int(cfg.get('interval', 600) or 600)
    except (ValueError, TypeError):
        cfg['interval'] = 600
    try:
        cfg['time_sync_interval'] = int(cfg.get('time_sync_interval', 3600) or 3600)
    except (ValueError, TypeError):
        cfg['time_sync_interval'] = 3600
    cfg['enabled'] = bool(cfg.get('enabled', False))
    cfg['time_sync_enabled'] = bool(cfg.get('time_sync_enabled', False))
    cfg['host'] = str(cfg.get('host', '')).strip()
    cfg['username'] = str(cfg.get('username', '')).strip()
    cfg['topic'] = str(cfg.get('topic', '') or '').strip() or SEND_TARGET_DEFAULT['topic']
    return cfg


def save_send_target_config(data):
    """送信先設定をファイルに保存。passwordが空のときは既存値を保持する。"""
    cur = load_send_target_config()
    pw = data.get('password', None)
    if pw is None or pw == '':
        pw = cur.get('password', '')
    cfg = {
        'enabled': bool(data.get('enabled', cur['enabled'])),
        'host': str(data.get('host', cur['host'])).strip(),
        'port': int(data.get('port', cur['port']) or 1883),
        'topic': str(data.get('topic', cur['topic'])).strip() or SEND_TARGET_DEFAULT['topic'],
        'username': str(data.get('username', cur['username'])).strip(),
        'password': pw,
        'interval': max(60, int(data.get('interval', cur['interval']) or 600)),
        'time_sync_enabled': bool(data.get('time_sync_enabled', cur['time_sync_enabled'])),
        'time_sync_interval': max(60, int(data.get('time_sync_interval', cur['time_sync_interval']) or 3600)),
    }
    with open(SEND_TARGET_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False)
    return cfg


def load_send_state():
    """ファイルごとの送信済み行数のdict {ファイル名: 行数} を読み込む"""
    if os.path.exists(SEND_TARGET_STATE_PATH):
        try:
            with open(SEND_TARGET_STATE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
            result = {}
            for k, v in (data.get('sent_lines', {}) or {}).items():
                try:
                    result[str(k)] = int(v)
                except (ValueError, TypeError):
                    pass
            return result
        except Exception:
            pass
    return {}


def save_send_state(sent_lines):
    """ファイルごとの送信済み行数を保存（既に存在しないファイルの記録は整理する）"""
    import glob
    existing = set(os.path.basename(p) for p in glob.glob(os.path.join(SEND_LOG_DIR, '*.csv')))
    pruned = {k: int(v) for k, v in sent_lines.items() if k in existing}
    try:
        with open(SEND_TARGET_STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'sent_lines': pruned}, f, ensure_ascii=False)
    except Exception:
        pass


# child-csv-to-mqtt.py 互換の送信形式。CSVの生1行ではなく、各行をパースしてJSONで送る。
# CSV1行: YYYYMMDDhhmmss,STA_NO1,STA_NO2,STA_NO3,T1_STATUS
SEND_SCHEMA_VERSION = 1
_SEND_MK_DATE_RE = re.compile(r'^\d{14}$')


def _send_device_id():
    """送信元の識別子。DEVICE_ID環境変数があれば優先、無ければホスト名。"""
    return os.environ.get('DEVICE_ID') or os.uname().nodename


def _csv_line_to_record(line, device_id):
    """CSV1行をchild-csv-to-mqtt.py互換のrecord dictに変換する。不正な行はNone。
    event_idは device_id+行内容 のSHA1で決定的に生成し、再送しても二重記録されない。"""
    parts = [p.strip() for p in line.strip().split(',')]
    if len(parts) != 5 or not parts[0]:
        return None
    mk_date, sta1, sta2, sta3, t1 = parts
    if not _SEND_MK_DATE_RE.match(mk_date):
        return None
    try:
        t1_status = int(t1)
    except ValueError:
        return None
    event_id = hashlib.sha1(
        '{}|{},{},{},{},{}'.format(device_id, mk_date, sta1, sta2, sta3, t1_status).encode('utf-8')
    ).hexdigest()
    return {
        'event_id': event_id,
        'mk_date': mk_date,
        'sta_no1': sta1,
        'sta_no2': sta2,
        'sta_no3': sta3,
        't1_status': t1_status,
        'device_id': device_id,
        'schema_version': SEND_SCHEMA_VERSION,
    }


def _read_complete_lines(filepath):
    """末尾が改行で終わる完全な行のみをリストで返す。
    書き込み途中の最終行（改行で終わらない）は確定するまで除外する。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return []
    if not content:
        return []
    # split('\n') の最後の要素は、末尾改行なら ''、未完なら書き込み途中の行。いずれも除外する。
    return content.split('\n')[:-1]


def _count_pending_lines():
    """未送信の行数の合計を返す（ステータス表示用）"""
    import glob
    state = load_send_state()
    total = 0
    for fp in glob.glob(os.path.join(SEND_LOG_DIR, '*.csv')):
        name = os.path.basename(fp)
        n = len(_read_complete_lines(fp))
        sent = state.get(name, 0)
        if n > sent:
            total += n - sent
    return total


def _publish_lines(cfg):
    """未送信の行をMQTTで1行ずつ送信する。(送信した行数, エラー文字列orNone) を返す。
    ファイルごとに送信済み行数を更新し、二重送信を防ぐ。"""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return 0, 'paho-mqtt未インストール（pip install paho-mqtt を実行してください）'
    import glob
    host = cfg.get('host', '').strip()
    if not host:
        return 0, '送信先(ブローカー)IPが未設定です'
    port = int(cfg.get('port', 1883) or 1883)
    topic = cfg.get('topic', '') or SEND_TARGET_DEFAULT['topic']
    username = cfg.get('username', '')
    password = cfg.get('password', '')

    state = load_send_state()
    # ファイル名先頭のタイムスタンプ順に古いものから送る
    targets = []  # (ファイル名, [未送信行...])
    for fp in sorted(glob.glob(os.path.join(SEND_LOG_DIR, '*.csv'))):
        name = os.path.basename(fp)
        lines = _read_complete_lines(fp)
        sent = state.get(name, 0)
        if len(lines) > sent:
            targets.append((name, lines[sent:]))
    if not targets:
        return 0, None

    try:
        # paho-mqtt 2.x は callback_api_version が必須。コールバックは使わないが
        # 旧版(1.x)互換のため VERSION1 を指定し、存在しなければ無指定で生成する。
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except (AttributeError, TypeError):
            client = mqtt.Client()
    except Exception as e:
        return 0, str(e)
    if username:
        client.username_pw_set(username, password)
    device_id = _send_device_id()
    sent_count = 0
    progressed = False  # 送信または不正行スキップで送信済みカウンタが進んだか
    err = None
    try:
        client.connect(host, port, keepalive=60)
        client.loop_start()
        for name, lines in targets:
            for line in lines:
                rec = _csv_line_to_record(line, device_id)
                if rec is None:
                    # 不正な行は送らないが、再処理しないようカウンタは進める
                    state[name] = state.get(name, 0) + 1
                    progressed = True
                    continue
                payload = json.dumps(rec)
                info = client.publish(topic, payload, qos=2)
                info.wait_for_publish(timeout=10)
                if not info.is_published():
                    raise RuntimeError('MQTT送信に失敗しました（{}）'.format(name))
                state[name] = state.get(name, 0) + 1
                sent_count += 1
                progressed = True
    except Exception as e:
        err = str(e)
    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass
    if progressed:
        save_send_state(state)
    return sent_count, err


def _update_send_status(result):
    with _send_status_lock:
        _send_status['last_time'] = time.strftime('%Y-%m-%d %H:%M:%S')
        _send_status['last_result'] = result


def do_send_logs(reason='manual'):
    """未送信の送信用ログの各行をMQTTブローカーへ送る。結果のdictを返す。
    同時実行は禁止（自動と手動が重なったら後発はスキップ）。"""
    if not _send_lock.acquire(blocking=False):
        return {'success': False, 'error': '送信処理が実行中です'}
    try:
        cfg = load_send_target_config()
        if not cfg.get('host'):
            return {'success': False, 'error': '送信先(ブローカー)IPが未設定です'}
        sent, err = _publish_lines(cfg)
        if err:
            msg = '{}行送信、エラー: {}'.format(sent, err)
            _update_send_status(msg)
            return {'success': sent > 0, 'sent': sent, 'error': err, 'message': msg}
        if sent == 0:
            return {'success': True, 'sent': 0, 'message': '送信対象の行がありません'}
        msg = '{}行送信成功'.format(sent)
        _update_send_status(msg)
        return {'success': True, 'sent': sent, 'message': msg}
    finally:
        _send_lock.release()


def _auto_send_worker():
    """有効時、定期的に未送信の送信用ログを自動送信するバックグラウンドスレッド"""
    # 起動直後の送信は避け、少し待ってから開始する
    time.sleep(30)
    while True:
        sleep_s = 60
        try:
            cfg = load_send_target_config()
            if cfg.get('enabled') and cfg.get('host'):
                do_send_logs(reason='auto')
                sleep_s = max(60, cfg.get('interval', 600))
        except Exception:
            pass
        time.sleep(sleep_s)


# --- 送信先ホストの時刻にPiのシステム時計を同期（SNTP） ---
# 送信先（MQTTブローカー）ホストのUDP123へSNTPクエリを投げて時刻を取得し、Piの
# システム時計を `sudo date` で合わせる。起動時＋定期（time_sync_interval秒）の自動
# 同期と、Webの手動ボタンに対応。システム時計の変更にはroot権限が必要。
_time_sync_lock = threading.Lock()
_time_sync_status_lock = threading.Lock()
_time_sync_status = {'last_time': '', 'last_result': ''}
NTP_UNIX_DELTA = 2208988800  # 1900-01-01 から 1970-01-01 までの秒数


def _query_sntp_time(host, port=123, timeout=5.0):
    """送信先ホストにSNTPクエリを投げ、サーバのUNIX時刻(float)を返す。失敗時は例外。"""
    import socket
    import struct
    # LI=0, VN=3, Mode=3(client) => 0x1B、残り47バイトは0
    pkt = b'\x1b' + 47 * b'\x00'
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(pkt, (host, port))
        data, _ = s.recvfrom(48)
    finally:
        s.close()
    if len(data) < 48:
        raise RuntimeError('SNTP応答が不正です')
    # Transmit Timestamp: バイト40-47（秒32bit＋小数32bit、基準1900年）
    secs, frac = struct.unpack('!II', data[40:48])
    if secs == 0:
        raise RuntimeError('SNTP応答の時刻が不正です')
    return (secs - NTP_UNIX_DELTA) + frac / 2 ** 32


def _set_system_time(unix_time):
    """システム時計を指定UNIX時刻に設定する（root権限が必要）。"""
    # date -s @<epoch> はUTCエポックとして解釈し、ローカルTZに依存せず設定できる
    r = subprocess.run(['sudo', 'date', '-s', '@{:.3f}'.format(unix_time)],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or 'date設定に失敗').strip())
    # RTC搭載機ではハードウェアクロックも合わせる（無RTCなら失敗するが無視）
    try:
        subprocess.run(['sudo', 'hwclock', '-w'], capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def _update_time_sync_status(result):
    with _time_sync_status_lock:
        _time_sync_status['last_time'] = time.strftime('%Y-%m-%d %H:%M:%S')
        _time_sync_status['last_result'] = result


def do_time_sync(reason='manual'):
    """送信先ホストの時刻を取得してPiのシステム時計を同期する。結果dictを返す。
    同時実行は禁止（自動と手動が重なったら後発はスキップ）。"""
    if not _time_sync_lock.acquire(blocking=False):
        return {'success': False, 'error': '時刻同期が実行中です'}
    try:
        cfg = load_send_target_config()
        host = cfg.get('host', '').strip()
        if not host:
            return {'success': False, 'error': '送信先(ブローカー)IPが未設定です'}
        try:
            server_unix = _query_sntp_time(host)
        except Exception as e:
            msg = '時刻取得失敗: {}'.format(e)
            _update_time_sync_status(msg)
            return {'success': False, 'error': msg}
        offset = server_unix - time.time()
        try:
            _set_system_time(server_unix)
        except Exception as e:
            msg = '時計設定失敗: {}'.format(e)
            _update_time_sync_status(msg)
            return {'success': False, 'error': msg}
        msg = '同期成功（補正 {:+.3f} 秒）'.format(offset)
        _update_time_sync_status(msg)
        return {'success': True, 'offset': offset,
                'server_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(server_unix)),
                'message': msg}
    finally:
        _time_sync_lock.release()


def _time_sync_worker():
    """有効時、起動時＋定期的に送信先ホストの時刻へ同期するバックグラウンドスレッド"""
    time.sleep(10)  # 起動直後の輻輳を避けて少し待つ
    first = True
    while True:
        sleep_s = 300
        try:
            cfg = load_send_target_config()
            if cfg.get('time_sync_enabled') and cfg.get('host'):
                do_time_sync(reason='startup' if first else 'auto')
                first = False
                sleep_s = max(60, cfg.get('time_sync_interval', 3600))
        except Exception:
            pass
        time.sleep(sleep_s)


# --- WiFiスキャン ---
# nmcli rescan はWiFi無線で実スキャンを行い一時的に通信を乱す＋数秒かかるため、
# リクエスト処理スレッド内で実行するとワーカーを占有し応答がハング（pending）する。
# rescanはバックグラウンドで実行してキャッシュを更新し、ハンドラは高速な list で
# 即座に現在値を返す設計とする。
_wifi_scan_lock = threading.Lock()
_wifi_scan_cache = {'networks': [], 'ts': 0.0}
_wifi_rescan_active = False


def _parse_nmcli_wifi_list(stdout):
    """`nmcli -f SSID,SIGNAL,SECURITY dev wifi list` の出力をパースする。"""
    networks = []
    lines = stdout.strip().split('\n')
    if len(lines) > 1:
        header = lines[0]
        sig_pos = header.find('SIGNAL')
        sec_pos = header.find('SECURITY')
        seen = set()
        for line in lines[1:]:
            if not line.strip():
                continue
            ssid = line[:sig_pos].strip() if sig_pos > 0 else ''
            signal = line[sig_pos:sec_pos].strip() if 0 < sig_pos < sec_pos else ''
            security = line[sec_pos:].strip() if sec_pos > 0 else ''
            if ssid and ssid not in seen:
                seen.add(ssid)
                networks.append({'ssid': ssid, 'signal': signal, 'security': security})
    return networks


def _wifi_list_now():
    """nmcli wifi list（rescanなし・高速）。失敗時は空リスト。"""
    try:
        r = subprocess.run(['nmcli', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return _parse_nmcli_wifi_list(r.stdout)
    except Exception:
        pass
    return []


def _wifi_rescan_worker():
    """バックグラウンドで再スキャン→list→キャッシュ更新。多重起動はガード。"""
    global _wifi_rescan_active
    try:
        subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'rescan'],
                       capture_output=True, text=True, timeout=20)
        time.sleep(3.0)   # NMの非同期スキャン完了を待つ
        nets = _wifi_list_now()
        if nets:
            with _wifi_scan_lock:
                _wifi_scan_cache['networks'] = nets
                _wifi_scan_cache['ts'] = time.time()
    except Exception:
        pass
    finally:
        _wifi_rescan_active = False


def _trigger_wifi_rescan():
    """バックグラウンド再スキャンを起動（実行中なら何もしない）。"""
    global _wifi_rescan_active
    with _wifi_scan_lock:
        if _wifi_rescan_active:
            return
        _wifi_rescan_active = True
    threading.Thread(target=_wifi_rescan_worker, daemon=True).start()


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # アクセスログは出力しない。
        # 理由: stdout→journald→SDカード書き込みの経路で、picamera.serviceの
        # 大量ログ等によりSD/journaldが詰まると、print()がSD I/O待ち(D state)で
        # ブロックし、stdoutの共有バッファロックを握ったまま全ワーカースレッドを
        # 連鎖停止させ、画像配信まで止まる。毎リクエストの出力を止めて回避する。
        # （メソッド自体は残す。削除するとデフォルト実装がstderrへ書き同じ問題になる）
        return

    def do_GET(self):
        # リクエスト処理時間と同時処理数を計測し、SLOW_REQ_SEC超のものだけ
        # /tmp(tmpfs)に記録する。間欠フリーズ時にどのエンドポイントが詰まり、
        # 何リクエストが同時に滞留していたかを後から特定するため。
        global _active_requests
        with _active_lock:
            _active_requests += 1
            active = _active_requests
        t0 = time.time()
        try:
            self._dispatch_get()
        finally:
            dt = time.time() - t0
            with _active_lock:
                _active_requests -= 1
            if dt >= SLOW_REQ_SEC:
                _log_slow_request(self.path, dt, active)

    def _dispatch_get(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        query = parse_qs(parsed_path.query)

        if path == '/' or path == '/index.html':
            self.serve_file('index.html', 'text/html')
        elif path == '/result.jpg':
            self.serve_result_image()
        elif path == '/switch_model':
            self.handle_switch_model(query)
        elif path == '/current_model':
            self.handle_current_model()
        elif path == '/model_status':
            self.handle_model_status()
        elif path == '/toggle_save':
            self.handle_toggle_save(query)
        elif path == '/save_settings':
            self.handle_save_settings()
        elif path == '/set_threshold':
            self.handle_set_threshold(query)
        elif path == '/get_threshold':
            self.handle_get_threshold()
        elif path == '/toggle_recognition':
            self.handle_toggle_recognition(query)
        elif path == '/recognition_settings':
            self.handle_recognition_settings()
        elif path == '/set_resolution':
            self.handle_set_resolution(query)
        elif path == '/set_camera_resolution':
            self.handle_set_camera_resolution(query)
        elif path == '/get_camera_resolution':
            self.handle_get_camera_resolution()
        elif path == '/set_signal_ids':
            self.handle_set_signal_ids()
        elif path == '/reset_signal_ids':
            self.handle_reset_signal_ids()
        elif path == '/shutdown':
            self.handle_shutdown()
        elif path == '/logs':
            self.handle_get_logs(query)
        elif path == '/change_logs':
            self.handle_get_change_logs(query)
        elif path == '/get_id_names':
            self.handle_get_id_names()
        elif path == '/get_status_codes':
            self.handle_get_status_codes()
        elif path == '/log_files':
            self.handle_get_log_files()
        elif path == '/download_log':
            self.handle_download_log(query)
        elif path == '/send_log_files':
            self.handle_get_send_log_files()
        elif path == '/download_send_log':
            self.handle_download_send_log(query)
        elif path == '/send_log_data':
            self.handle_get_send_log_data(query)
        elif path == '/send_target_config':
            self.handle_get_send_target_config()
        elif path == '/send_target_status':
            self.handle_get_send_target_status()
        elif path == '/time_sync_status':
            self.handle_get_time_sync_status()
        elif path == '/image_files':
            self.handle_get_image_files(query)
        elif path == '/image_file':
            self.handle_image_file(query)
        elif path == '/log_file_data':
            self.handle_get_log_file_data(query)
        elif path == '/all_settings':
            self.handle_get_all_settings()
        elif path == '/wifi_status':
            self.handle_wifi_status()
        elif path == '/wifi_scan':
            self.handle_wifi_scan(query)
        elif path == '/ip_config':
            self.handle_get_ip_config()
        elif path == '/confirm_ip_config':
            self.handle_confirm_ip_config()
        else:
            self.send_error(404, 'File not found')

    def serve_file(self, filename, content_type):
        filepath = os.path.join(WEB_DIR, filename)
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-type', content_type)
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, 'File not found')

    def serve_result_image(self):
        try:
            with open(RESULT_IMAGE_PATH, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-type', 'image/jpeg')
            self.send_header('Content-Length', len(content))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            # 画像がない場合はプレースホルダー画像を生成
            self.send_response(200)
            self.send_header('Content-type', 'image/jpeg')
            placeholder = self.create_placeholder_image()
            self.send_header('Content-Length', len(placeholder))
            self.end_headers()
            self.wfile.write(placeholder)

    def create_placeholder_image(self):
        """プレースホルダー画像を生成"""
        import struct
        import zlib
        # 簡単な1x1ピクセルの黒いJPEG画像
        # 実際には「カメラ待機中」のテキスト画像を作成
        try:
            import cv2
            import numpy as np
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            img[:] = (34, 34, 34)  # 暗いグレー背景
            cv2.putText(img, "Waiting for camera...", (150, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (128, 128, 128), 2)
            _, jpeg = cv2.imencode('.jpg', img)
            return jpeg.tobytes()
        except:
            # OpenCVがない場合は最小限のJPEG
            return bytes([
                0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
                0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
                0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
                0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
                0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
                0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
                0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
                0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
                0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
                0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
                0x09, 0x0A, 0x0B, 0xFF, 0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
                0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
                0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
                0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
                0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
                0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
                0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
                0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
                0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
                0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
                0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
                0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
                0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
                0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
                0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
                0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
                0x00, 0x00, 0x3F, 0x00, 0xFB, 0xD5, 0xDB, 0x20, 0xA8, 0xBA, 0xB3, 0x33,
                0x1A, 0x89, 0x21, 0x68, 0xC6, 0x32, 0x30, 0x7F, 0xFC, 0xD3, 0xFF, 0xD9
            ])

    def handle_switch_model(self, query):
        global current_model
        model_type = query.get('model', ['object_detection'])[0]

        if model_type in MODELS:
            if model_type == current_model:
                # 同じモデルが実行中の場合は切り替え不要
                response = {
                    'success': True,
                    'model': model_type,
                    'same_model': True
                }
            else:
                current_model = model_type
                config = save_model_config(model_type)
                response = {
                    'success': True,
                    'model': model_type,
                    'config': config
                }
        else:
            response = {
                'success': False,
                'error': f'Unknown model: {model_type}'
            }

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_current_model(self):
        config = load_model_config()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(config).encode())

    def handle_model_status(self):
        status = load_model_status()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())

    def handle_toggle_save(self, query):
        save_type = query.get('type', ['result'])[0]
        config = load_save_config()

        if save_type == 'result':
            config['save_result'] = not config.get('save_result', True)
        elif save_type == 'raw':
            config['save_raw'] = not config.get('save_raw', True)

        save_save_config(config['save_result'], config['save_raw'])

        response = {
            'success': True,
            'type': save_type,
            'enabled': config.get(f'save_{save_type}', True)
        }

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_save_settings(self):
        config = load_save_config()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(config).encode())

    def handle_set_threshold(self, query):
        try:
            threshold = float(query.get('value', ['0.25'])[0])
            # しきい値の範囲をチェック (0.25 ~ 0.95)
            threshold = max(0.25, min(0.95, threshold))
            save_threshold_config(threshold)
            print(f"[SET_THRESHOLD] しきい値を {threshold:.3f} に設定")
            response = {
                'success': True,
                'threshold': threshold
            }
        except ValueError:
            response = {
                'success': False,
                'error': 'Invalid threshold value'
            }

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_get_threshold(self):
        config = load_threshold_config()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(config).encode())

    def handle_toggle_recognition(self, query):
        config = load_recognition_config()
        # value指定があればその値を使用、なければトグル
        value = query.get('value', [None])[0]
        if value is not None:
            config['enabled'] = value == 'true'
        else:
            config['enabled'] = not config.get('enabled', True)
        save_recognition_config(config['enabled'], config.get('resolution', '640x640'))
        save_model_status('switching', 'recognition')
        response = {
            'success': True,
            'enabled': config['enabled'],
            'resolution': config.get('resolution', 'full')
        }
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_recognition_settings(self):
        config = load_recognition_config()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(config).encode())

    def handle_set_resolution(self, query):
        resolution = query.get('value', ['100'])[0]
        try:
            val = int(resolution)
            resolution = str(max(1, min(100, val)))  # 1〜100%にクランプ
        except ValueError:
            resolution = '100'
        config = load_recognition_config()
        config['resolution'] = resolution
        save_recognition_config(config['enabled'], resolution)
        # ScalerCropによる即時反映のためステータス変更不要
        response = {
            'success': True,
            'resolution': resolution
        }
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())


    def handle_set_camera_resolution(self, query):
        """カメラ解像度を設定して再起動を促す"""
        VALID_RESOLUTIONS = ['320x240', '640x640', '1920x1080']
        resolution = query.get('value', ['1920x1080'])[0]
        if resolution not in VALID_RESOLUTIONS:
            response = {'success': False, 'error': f'Invalid resolution: {resolution}'}
        else:
            config = load_recognition_config()
            config['camera_resolution'] = resolution
            save_recognition_config(config['enabled'], '100', resolution)  # クロップを全画角にリセット
            save_model_status('switching', config.get('model_type', ''))
            print(f"[CAMERA_RESOLUTION] カメラ解像度を {resolution} に設定（再起動待ち）")
            response = {'success': True, 'camera_resolution': resolution}
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_get_camera_resolution(self):
        """現在のカメラ解像度設定を返す"""
        config = load_recognition_config()
        response = {'camera_resolution': config.get('camera_resolution', '1920x1080')}
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_set_signal_ids(self):
        """シグナルタワーID設定トリガー"""
        try:
            with open('/tmp/set_signal_ids', 'w') as f:
                f.write('1')
            response = {'success': True}
        except Exception as e:
            response = {'success': False, 'error': str(e)}
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_reset_signal_ids(self):
        """シグナルタワーID領域リセットトリガー"""
        try:
            with open('/tmp/reset_signal_ids', 'w') as f:
                f.write('1')
            response = {'success': True}
        except Exception as e:
            response = {'success': False, 'error': str(e)}
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_get_logs(self, query):
        """最新100件のログエントリをJSONで返す（model_typeでフィルタ）"""
        import glob
        import csv

        model_type = query.get('model_type', [''])[0]
        logs_dir = '/home/pi/logs/'
        all_entries = []

        if model_type == 'object_detection':
            pattern = os.path.join(logs_dir, 'log_object_detection_*.csv')
            label = '物体認識'
        elif model_type == 'signal_tower':
            pattern = os.path.join(logs_dir, 'log_signal_tower_*.csv')
            label = 'シグナル'
        else:
            pattern = os.path.join(logs_dir, '*.csv')
            label = None

        log_files = sorted(glob.glob(pattern), reverse=True)

        for filepath in log_files:
            if len(all_entries) >= 100:
                break
            if label is None:
                is_object = 'object_detection' in os.path.basename(filepath)
                row_label = '物体認識' if is_object else 'シグナル'
            else:
                row_label = label
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                if len(rows) < 2:
                    continue
                for row in rows[1:]:
                    if row:
                        all_entries.append({
                            'datetime': row[0],
                            'type': row_label,
                            'data': row[1:]
                        })
            except Exception:
                continue

        all_entries.sort(key=lambda x: x['datetime'], reverse=True)
        latest = all_entries[:100]

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'entries': latest}, ensure_ascii=False).encode())

    def handle_get_change_logs(self, query):
        """変化ログファイル(log_changes_*.csv)の最新100件をJSONで返す"""
        import glob
        import csv

        model_type = query.get('model_type', [''])[0]
        logs_dir = '/home/pi/logs/'

        if model_type == 'signal_tower':
            pattern = os.path.join(logs_dir, 'log_changes_signal_tower_*.csv')
        elif model_type == 'object_detection':
            pattern = os.path.join(logs_dir, 'log_changes_object_detection_*.csv')
        else:
            pattern = os.path.join(logs_dir, 'log_changes_*.csv')

        log_files = sorted(glob.glob(pattern), reverse=True)
        all_entries = []

        for filepath in log_files:
            if len(all_entries) >= 100:
                break
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        all_entries.append(dict(row))
            except Exception:
                continue

        all_entries.sort(key=lambda x: x.get('datetime', ''), reverse=True)
        latest = all_entries[:100]

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'entries': latest}, ensure_ascii=False).encode())

    def handle_shutdown(self):
        """システムシャットダウンを実行"""
        response = {'success': True, 'message': 'Shutting down...'}
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())
        print("[SHUTDOWN] シャットダウンコマンドを実行します")
        subprocess.Popen(['sudo', 'shutdown', '-h', 'now'])

    def handle_get_log_file_data(self, query):
        """指定ログファイルのデータをJSONで返す（グラフ描画用）"""
        import csv
        filename = query.get('file', [''])[0]
        if not filename or '/' in filename or '..' in filename:
            self.send_error(400, 'Invalid filename')
            return
        logs_dir = '/home/pi/logs/'
        filepath = os.path.join(logs_dir, filename)
        if not os.path.isfile(filepath):
            self.send_error(404, 'File not found')
            return
        entries = []
        if 'log_changes_signal_tower' in filename:
            file_type = 'signal_changes'
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        entries.append(dict(row))
            except Exception as e:
                self.send_error(500, str(e)); return
        elif 'log_changes_object_detection' in filename:
            file_type = 'object_changes'
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        entries.append(dict(row))
            except Exception as e:
                self.send_error(500, str(e)); return
        elif 'log_signal_tower' in filename:
            file_type = 'signal_log'
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                for row in rows[1:]:
                    if row:
                        entries.append({'datetime': row[0], 'data': row[1:]})
            except Exception as e:
                self.send_error(500, str(e)); return
        else:
            file_type = 'object_log'
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                for row in rows[1:]:
                    if row:
                        entries.append({'datetime': row[0], 'data': row[1:]})
            except Exception as e:
                self.send_error(500, str(e)); return
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(
            {'type': file_type, 'entries': entries}, ensure_ascii=False
        ).encode())

    def handle_get_image_files(self, query):
        """保存画像ファイル一覧をJSONで返す（type=result|raw）"""
        import glob, datetime
        img_type = query.get('type', ['result'])[0]
        if img_type == 'raw':
            images_dir = '/home/pi/raw_images/'
            pattern = os.path.join(images_dir, '*.jpg')
        else:
            images_dir = '/home/pi/images/'
            pattern = os.path.join(images_dir, '*.jpg')
        files = []
        for filepath in sorted(glob.glob(pattern), reverse=True):
            try:
                stat = os.stat(filepath)
                size_kb = stat.st_size / 1024
                if size_kb >= 1024:
                    size_str = '{:.1f} MB'.format(size_kb / 1024)
                else:
                    size_str = '{:.1f} KB'.format(size_kb)
                modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                files.append({
                    'name': os.path.basename(filepath),
                    'size': size_str,
                    'modified': modified
                })
            except Exception:
                continue
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'files': files}, ensure_ascii=False).encode())

    def handle_image_file(self, query):
        """指定画像ファイルを配信する（type=result|raw、dl=1でダウンロード）"""
        filename = query.get('file', [''])[0]
        img_type = query.get('type', ['result'])[0]
        dl = query.get('dl', ['0'])[0]
        if not filename or '/' in filename or '..' in filename:
            self.send_error(400, 'Invalid filename')
            return
        if img_type == 'raw':
            images_dir = '/home/pi/raw_images/'
        else:
            images_dir = '/home/pi/images/'
        filepath = os.path.join(images_dir, filename)
        if not os.path.isfile(filepath):
            self.send_error(404, 'File not found')
            return
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-type', 'image/jpeg')
            if dl == '1':
                self.send_header('Content-Disposition', 'attachment; filename="{}"'.format(filename))
            else:
                self.send_header('Content-Disposition', 'inline; filename="{}"'.format(filename))
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def handle_get_log_files(self):
        """ログファイル一覧をJSONで返す"""
        import glob
        logs_dir = '/home/pi/logs/'
        pattern = os.path.join(logs_dir, '*.csv')
        files = []
        for filepath in sorted(glob.glob(pattern), reverse=True):
            try:
                stat = os.stat(filepath)
                size_kb = stat.st_size / 1024
                if size_kb >= 1024:
                    size_str = '{:.1f} MB'.format(size_kb / 1024)
                else:
                    size_str = '{:.1f} KB'.format(size_kb)
                import datetime
                modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                files.append({
                    'name': os.path.basename(filepath),
                    'size': size_str,
                    'modified': modified
                })
            except Exception:
                continue
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'files': files}, ensure_ascii=False).encode())

    def handle_download_log(self, query):
        """指定ログファイルをダウンロードさせる"""
        filename = query.get('file', [''])[0]
        if not filename or '/' in filename or '..' in filename:
            self.send_error(400, 'Invalid filename')
            return
        logs_dir = '/home/pi/logs/'
        filepath = os.path.join(logs_dir, filename)
        if not os.path.isfile(filepath):
            self.send_error(404, 'File not found')
            return
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="{}"'.format(filename))
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def handle_get_send_log_files(self):
        """送信用ログファイル一覧をJSONで返す"""
        import glob
        pattern = os.path.join(SEND_LOG_DIR, '*.csv')
        files = []
        for filepath in sorted(glob.glob(pattern), reverse=True):
            try:
                stat = os.stat(filepath)
                size_kb = stat.st_size / 1024
                if size_kb >= 1024:
                    size_str = '{:.1f} MB'.format(size_kb / 1024)
                else:
                    size_str = '{:.1f} KB'.format(size_kb)
                import datetime
                modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                files.append({
                    'name': os.path.basename(filepath),
                    'size': size_str,
                    'modified': modified
                })
            except Exception:
                continue
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'files': files}, ensure_ascii=False).encode())

    def handle_download_send_log(self, query):
        """指定送信用ログファイルをダウンロードさせる"""
        filename = query.get('file', [''])[0]
        if not filename or '/' in filename or '..' in filename:
            self.send_error(400, 'Invalid filename')
            return
        filepath = os.path.join(SEND_LOG_DIR, filename)
        if not os.path.isfile(filepath):
            self.send_error(404, 'File not found')
            return
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="{}"'.format(filename))
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def handle_get_send_log_data(self, query):
        """指定送信用ログファイルをパースし、状態コード時系列グラフ用のJSONを返す。
        各行 `日時(YYYYMMDDhhmmss),名前1,名前2,名前3,状態コード` を
        {datetime: 'YYYY-MM-DD HH:MM:SS', name: 名前を空項目除外でスペース連結, code: int}
        に変換する（datetimeはJS側のグラフ描画が解釈できる形式に整形）。"""
        import csv
        filename = query.get('file', [''])[0]
        if not filename or '/' in filename or '..' in filename:
            self.send_error(400, 'Invalid filename')
            return
        filepath = os.path.join(SEND_LOG_DIR, filename)
        if not os.path.isfile(filepath):
            self.send_error(404, 'File not found')
            return
        entries = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for row in csv.reader(f):
                    if len(row) < 5:
                        continue
                    ts = row[0].strip()
                    if len(ts) != 14 or not ts.isdigit():
                        continue
                    dt = '{}-{}-{} {}:{}:{}'.format(
                        ts[0:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12], ts[12:14])
                    name = ' '.join([p for p in (row[1].strip(), row[2].strip(), row[3].strip()) if p])
                    try:
                        code = int(row[4])
                    except (ValueError, TypeError):
                        continue
                    entries.append({'datetime': dt, 'name': name, 'code': code})
        except Exception as e:
            self.send_error(500, str(e))
            return
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(
            {'type': 'send_log', 'entries': entries}, ensure_ascii=False
        ).encode())

    def handle_get_send_target_config(self):
        """送信先設定をJSONで返す（パスワードは含めず、設定有無のみ返す）"""
        cfg = load_send_target_config()
        cfg.pop('password', None)
        cfg['has_password'] = bool(load_send_target_config().get('password'))
        self._send_json(cfg)

    def handle_get_send_target_status(self):
        """送信状態（最終送信時刻・結果・未送信件数）をJSONで返す"""
        with _send_status_lock:
            st = dict(_send_status)
        try:
            st['pending'] = _count_pending_lines()
        except Exception:
            st['pending'] = 0
        self._send_json(st)

    def handle_set_send_target_config(self):
        """送信先設定を保存する"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            save_send_target_config(data)
            self._send_json({'success': True})
        except Exception as e:
            self._send_json({'success': False, 'error': str(e)})

    def handle_send_logs_now(self):
        """未送信の送信用ログを今すぐ送信する（手動送信ボタン）"""
        try:
            result = do_send_logs(reason='manual')
        except Exception as e:
            result = {'success': False, 'error': str(e)}
        self._send_json(result)

    def handle_get_time_sync_status(self):
        """時刻同期の状態（最終同期時刻・結果）をJSONで返す"""
        with _time_sync_status_lock:
            st = dict(_time_sync_status)
        self._send_json(st)

    def handle_time_sync_now(self):
        """送信先ホストの時刻にPiのシステム時計を今すぐ同期する（手動ボタン）"""
        try:
            result = do_time_sync(reason='manual')
        except Exception as e:
            result = {'success': False, 'error': str(e)}
        self._send_json(result)

    def handle_get_id_names(self):
        """ID名前一覧をJSONで返す"""
        id_names = load_id_names_config()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'id_names': id_names}, ensure_ascii=False).encode())

    def handle_get_status_codes(self):
        """状態コードのパターン定義をJSONで返す"""
        config = load_status_code_config()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(config, ensure_ascii=False).encode())

    def do_POST(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/set_id_names':
            self.handle_set_id_names()
        elif path == '/set_status_codes':
            self.handle_set_status_codes()
        elif path == '/wifi_connect':
            self.handle_wifi_connect()
        elif path == '/set_ip_config':
            self.handle_set_ip_config()
        elif path == '/set_send_target_config':
            self.handle_set_send_target_config()
        elif path == '/send_logs_now':
            self.handle_send_logs_now()
        elif path == '/time_sync_now':
            self.handle_time_sync_now()
        else:
            self.send_error(404, 'Not found')

    def handle_set_id_names(self):
        """ID名前一覧をファイルに保存する"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            id_names = data.get('id_names', {})
            save_id_names_config(id_names)
            response = {'success': True}
        except Exception as e:
            response = {'success': False, 'error': str(e)}
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def handle_set_status_codes(self):
        """状態コードのパターン定義をファイルに保存する"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            save_status_code_config(data)
            response = {'success': True}
        except Exception as e:
            response = {'success': False, 'error': str(e)}
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())


    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
        self.wfile.write(body)

    def handle_get_all_settings(self):
        """保存設定・しきい値・認識設定をまとめて返す（タッチパネル同期用）"""
        save_cfg = load_save_config()
        thr_cfg = load_threshold_config()
        rec_cfg = load_recognition_config()
        response = {
            'save_result': save_cfg.get('save_result', True),
            'save_raw': save_cfg.get('save_raw', True),
            'threshold': thr_cfg.get('threshold', 0.25),
            'recognition_enabled': rec_cfg.get('enabled', True),
            'resolution': rec_cfg.get('resolution', '100'),
        }
        self._send_json(response)

    def handle_wifi_status(self):
        """現在のWiFi接続状態を返す"""
        ssid = ''
        ip = ''
        try:
            r = subprocess.run(['iwgetid', '-r'], capture_output=True, text=True, timeout=5)
            ssid = r.stdout.strip()
        except Exception:
            pass
        try:
            r = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
            ips = r.stdout.strip().split()
            ip = ips[0] if ips else ''
        except Exception:
            pass
        self._send_json({'ssid': ssid, 'ip': ip})

    def handle_wifi_scan(self, query=None):
        """利用可能なWiFiネットワークを返す（ハンドラはブロックしない）。
        - nmcliの即時listとバックグラウンド再スキャンのキャッシュを統合して返す。
        - rescanは別スレッドで実行するため、初回は接続中AP程度しか出ないことがあるが、
          数秒後の再取得で全APが揃う（フロントが自動で再取得する）。
        - nmcliが空のときのみ wpa_cli / iwlist にフォールバックする。"""
        # 即時list（rescanなし・高速）と背景rescanの起動
        networks = _wifi_list_now()
        _trigger_wifi_rescan()
        # 背景rescanの結果が新しく件数が多ければそちらを採用
        with _wifi_scan_lock:
            cached = list(_wifi_scan_cache['networks'])
        if len(cached) > len(networks):
            networks = cached

        # nmcliが何も返さない環境向けフォールバック（wpa_cli → iwlist）
        if not networks:
            try:
                subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'scan'],
                               capture_output=True, text=True, timeout=10)
                time.sleep(2.5)
                r = subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'scan_results'],
                                   capture_output=True, text=True, timeout=10)
                best = {}  # ssid -> (signal_pct, security)
                for line in r.stdout.split('\n'):
                    cols = line.split('\t')
                    if len(cols) < 5:
                        continue
                    try:
                        dbm = int(cols[2])
                    except ValueError:
                        continue
                    flags = cols[3]
                    ssid = cols[4].strip()
                    if not ssid:
                        continue
                    pct = max(0, min(100, 2 * (dbm + 100)))
                    if 'WPA2' in flags:
                        security = 'WPA2'
                    elif 'WPA' in flags:
                        security = 'WPA'
                    elif 'WEP' in flags:
                        security = 'WEP'
                    else:
                        security = ''
                    if ssid not in best or pct > best[ssid][0]:
                        best[ssid] = (pct, security)
                for ssid, (pct, security) in best.items():
                    networks.append({'ssid': ssid, 'signal': str(pct), 'security': security})
            except Exception:
                pass

        if not networks:
            try:
                r = subprocess.run(['sudo', 'iwlist', 'wlan0', 'scan'],
                                   capture_output=True, text=True, timeout=20)
                ssids = re.findall(r'ESSID:"([^"]*)"', r.stdout)
                qualities = re.findall(r'Quality=(\d+)/(\d+)', r.stdout)
                encrs = re.findall(r'Encryption key:(on|off)', r.stdout)
                seen = set()
                for i, ssid in enumerate(ssids):
                    if not ssid or ssid in seen:
                        continue
                    seen.add(ssid)
                    signal = ''
                    if i < len(qualities):
                        q, mx = qualities[i]
                        signal = str(round(int(q) / int(mx) * 100))
                    security = 'WPA' if i < len(encrs) and encrs[i] == 'on' else ''
                    networks.append({'ssid': ssid, 'signal': signal, 'security': security})
            except Exception:
                pass

        networks.sort(
            key=lambda x: int(x['signal']) if x['signal'].isdigit() else 0,
            reverse=True
        )
        self._send_json({'networks': networks})

    def handle_wifi_connect(self):
        """WiFiネットワークに接続する（非同期実行）"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
        except Exception as e:
            self._send_json({'success': False, 'error': str(e)})
            return

        ssid = data.get('ssid', '').strip()
        password = data.get('password', '')

        if not ssid:
            self._send_json({'success': False, 'error': 'SSIDを入力してください'})
            return

        # レスポンスを先に返してから非同期で接続処理を行う
        self._send_json({'success': True})

        def _apply():
            time.sleep(1)
            # nmcli が使えるか確認
            if subprocess.run(['which', 'nmcli'], capture_output=True).returncode == 0:
                args = ['sudo', 'nmcli', 'dev', 'wifi', 'connect', ssid]
                if password:
                    args += ['password', password]
                subprocess.run(args, capture_output=True, timeout=30)
            else:
                # wpa_supplicant フォールバック
                try:
                    config_path = '/etc/wpa_supplicant/wpa_supplicant.conf'
                    try:
                        with open(config_path, 'r') as f:
                            content = f.read()
                    except Exception:
                        content = ('ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n'
                                   'update_config=1\ncountry=JP\n')
                    content = re.sub(r'\n*network=\{[^}]*\}', '', content).strip()
                    if password:
                        block = ('\n\nnetwork={\n\tssid="' + ssid + '"\n\tpsk="' + password +
                                 '"\n\tkey_mgmt=WPA-PSK\n\tpriority=10\n}')
                    else:
                        block = '\n\nnetwork={\n\tssid="' + ssid + '"\n\tkey_mgmt=NONE\n\tpriority=10\n}'
                    tmp = '/tmp/wpa_supplicant_tmp.conf'
                    with open(tmp, 'w') as f:
                        f.write(content + block)
                    subprocess.run(['sudo', 'cp', tmp, config_path])
                    subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'reconfigure'], timeout=10)
                except Exception:
                    pass

        threading.Thread(target=_apply, daemon=True).start()

    # --- 固定IP設定 ---
    def handle_get_ip_config(self):
        """現在のIP設定（IP・プレフィックス・ゲートウェイ・DNS・取得方式）を返す"""
        iface = _net_primary_iface()
        ip = ''
        prefix = 24
        gateway = ''
        dns = ''
        method = 'auto'
        try:
            r = subprocess.run(['ip', '-o', '-f', 'inet', 'addr', 'show', iface],
                               capture_output=True, text=True, timeout=5)
            m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/(\d+)', r.stdout)
            if m:
                ip = m.group(1)
                prefix = int(m.group(2))
        except Exception:
            pass
        try:
            r = subprocess.run(['ip', 'route', 'show', 'default'],
                               capture_output=True, text=True, timeout=5)
            m = re.search(r'default via (\d+\.\d+\.\d+\.\d+)', r.stdout)
            if m:
                gateway = m.group(1)
        except Exception:
            pass
        con = _net_active_con(iface)
        if con:
            try:
                r = subprocess.run(['nmcli', '-t', '-f', 'ipv4.method,ipv4.dns', 'connection', 'show', con],
                                   capture_output=True, text=True, timeout=10)
                for line in r.stdout.strip().split('\n'):
                    if line.startswith('ipv4.method:'):
                        method = line.split(':', 1)[1].strip() or 'auto'
                    elif line.startswith('ipv4.dns:'):
                        dns = line.split(':', 1)[1].strip()
            except Exception:
                pass
        self._send_json({'interface': iface, 'ip': ip, 'prefix': prefix,
                         'gateway': gateway, 'dns': dns, 'method': method})

    def handle_set_ip_config(self):
        """固定IP/DHCPを設定する（応答を先に返し、変更はバックグラウンドで非同期実行）。
        固定IP設定時は IP_REVERT_DELAY 秒以内に /confirm_ip_config で確認されなければ
        自動でDHCPへ復帰し、不正なIPによるロックアウトを防ぐ。"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
        except Exception as e:
            self._send_json({'success': False, 'error': str(e)})
            return

        mode = data.get('mode', 'static')
        ip = str(data.get('ip', '')).strip()
        gateway = str(data.get('gateway', '')).strip()
        dns = str(data.get('dns', '')).strip()
        try:
            prefix = int(data.get('prefix', 24))
        except (ValueError, TypeError):
            prefix = 24

        dns_norm = ''
        if mode == 'static':
            if not _valid_ipv4(ip):
                self._send_json({'success': False, 'error': 'IPアドレスが不正です'})
                return
            if not (1 <= prefix <= 32):
                self._send_json({'success': False, 'error': 'プレフィックス長が不正です（1〜32）'})
                return
            if gateway and not _valid_ipv4(gateway):
                self._send_json({'success': False, 'error': 'ゲートウェイが不正です'})
                return
            dns_list = [d for d in re.split(r'[\s,]+', dns) if d]
            for d in dns_list:
                if not _valid_ipv4(d):
                    self._send_json({'success': False, 'error': 'DNSが不正です'})
                    return
            dns_norm = ' '.join(dns_list)
        elif mode != 'dhcp':
            self._send_json({'success': False, 'error': 'modeが不正です'})
            return

        # 応答を先に返してから非同期で適用（IP変更で接続が切れるため）
        self._send_json({'success': True, 'revert_sec': IP_REVERT_DELAY if mode == 'static' else 0})

        iface = _net_primary_iface()

        def _apply():
            time.sleep(1)
            if mode == 'static':
                _net_apply_ip('static', ip, prefix, gateway, dns_norm, iface)
                # 新IPで再接続→/confirm_ip_config が来なければ自動でDHCPに戻す（ロックアウト防止）
                _schedule_ip_revert(iface)
            else:
                _cancel_ip_revert()
                _net_apply_ip('dhcp', '', 0, '', '', iface)

        threading.Thread(target=_apply, daemon=True).start()

    def handle_confirm_ip_config(self):
        """固定IP設定の確認（新IPで再接続できた合図）。自動DHCP復帰タイマーをキャンセルする。"""
        canceled = _cancel_ip_revert()
        self._send_json({'success': True, 'pending': canceled})


# --- ネットワーク（固定IP/DHCP）設定の共通処理 ---
# 固定IP適用後、確認(/confirm_ip_config)が無ければ自動でDHCPに戻すまでの秒数。
# 不正なIPを設定してWebに二度と入れなくなる事故を防ぐための安全機構。
IP_REVERT_DELAY = 60
_ip_revert = {'timer': None}


def _valid_ipv4(s):
    parts = s.split('.')
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _net_primary_iface():
    """デフォルトルートが向いているインターフェース名を返す（既定: wlan0）"""
    try:
        r = subprocess.run(['ip', 'route', 'show', 'default'],
                           capture_output=True, text=True, timeout=5)
        m = re.search(r'dev (\S+)', r.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return 'wlan0'


def _net_active_con(iface):
    """指定インターフェースのアクティブなNetworkManager接続名を返す（無ければNone）"""
    try:
        r = subprocess.run(['nmcli', '-t', '-f', 'NAME,DEVICE', 'connection', 'show', '--active'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            fallback = None
            for line in r.stdout.strip().split('\n'):
                if not line:
                    continue
                # NAME中のコロンは '\:' でエスケープされるため右側から1回だけ分割
                name, _, dev = line.rpartition(':')
                if not name:
                    name = dev
                name = name.replace('\\:', ':')
                if dev == iface:
                    return name
                if fallback is None and dev != 'lo':
                    fallback = name
            return fallback
    except Exception:
        pass
    return None


def _net_apply_ip(mode, ip, prefix, gateway, dns_norm, iface):
    """nmcli優先・dhcpcdフォールバックで固定IP/DHCPを適用する"""
    con = _net_active_con(iface)
    if subprocess.run(['which', 'nmcli'], capture_output=True).returncode == 0 and con:
        if mode == 'static':
            args = ['sudo', 'nmcli', 'connection', 'modify', con,
                    'ipv4.addresses', '{}/{}'.format(ip, prefix), 'ipv4.method', 'manual']
            if gateway:
                args += ['ipv4.gateway', gateway]
            if dns_norm:
                args += ['ipv4.dns', dns_norm]
            subprocess.run(args, capture_output=True, timeout=30)
        else:
            subprocess.run(['sudo', 'nmcli', 'connection', 'modify', con,
                            'ipv4.method', 'auto',
                            'ipv4.addresses', '', 'ipv4.gateway', '', 'ipv4.dns', ''],
                           capture_output=True, timeout=30)
        subprocess.run(['sudo', 'nmcli', 'connection', 'up', con], capture_output=True, timeout=30)
    else:
        # dhcpcd フォールバック（/etc/dhcpcd.conf をマーカーで管理）
        try:
            conf = '/etc/dhcpcd.conf'
            try:
                with open(conf, 'r') as f:
                    content = f.read()
            except Exception:
                content = ''
            content = re.sub(r'\n*# >>> picamera static ip >>>.*?# <<< picamera static ip <<<\n*',
                             '\n', content, flags=re.DOTALL).rstrip()
            if mode == 'static':
                block = ['', '# >>> picamera static ip >>>', 'interface {}'.format(iface),
                         'static ip_address={}/{}'.format(ip, prefix)]
                if gateway:
                    block.append('static routers={}'.format(gateway))
                if dns_norm:
                    block.append('static domain_name_servers={}'.format(dns_norm))
                block.append('# <<< picamera static ip <<<')
                content = content + '\n' + '\n'.join(block) + '\n'
            else:
                content = content + '\n'
            tmp = '/tmp/dhcpcd_tmp.conf'
            with open(tmp, 'w') as f:
                f.write(content)
            subprocess.run(['sudo', 'cp', tmp, conf], timeout=10)
            if subprocess.run(['which', 'dhcpcd'], capture_output=True).returncode == 0:
                subprocess.run(['sudo', 'systemctl', 'restart', 'dhcpcd'], timeout=30)
        except Exception:
            pass


def _cancel_ip_revert():
    """自動DHCP復帰タイマーをキャンセルする（保留中だったらTrue）"""
    t = _ip_revert.get('timer')
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass
        _ip_revert['timer'] = None
        return True
    return False


def _schedule_ip_revert(iface):
    """確認が無ければ IP_REVERT_DELAY 秒後にDHCPへ自動復帰するタイマーを開始する"""
    _cancel_ip_revert()
    def _revert():
        _ip_revert['timer'] = None
        print('[IP] 確認が無いためDHCPに自動復帰します')
        _net_apply_ip('dhcp', '', 0, '', '', iface)
    t = threading.Timer(IP_REVERT_DELAY, _revert)
    t.daemon = True
    t.start()
    _ip_revert['timer'] = t


def main():
    # 起動時に設定を読み込み
    load_model_config()

    # 送信用ログの自動送信ワーカーを起動
    threading.Thread(target=_auto_send_worker, daemon=True).start()

    # 送信先ホストの時刻にシステム時計を同期するワーカーを起動（起動時＋定期）
    threading.Thread(target=_time_sync_worker, daemon=True).start()

    # ファイルパスの確認
    index_path = os.path.join(WEB_DIR, 'index.html')
    print(f"WEB_DIR: {WEB_DIR}")
    print(f"index.html: {index_path} (存在: {os.path.exists(index_path)})")
    print(f"result.jpg: {RESULT_IMAGE_PATH} (存在: {os.path.exists(RESULT_IMAGE_PATH)})")

    # 上限付きスレッドプールで処理する。
    # ・シングルスレッドだと画像取得(1秒ごと)が他リクエストで詰まり更新が止まる。
    # ・ThreadingHTTPServer(無制限スレッド)はPi Zero 2WのTasksMax制限下で
    #   スレッドが溜まると枯渇しアクセプトが停止する(=しばらく後に画像停止)。
    # 固定ワーカー数で両方を回避する。
    server = PooledHTTPServer(('', PORT), RequestHandler, max_workers=6)
    print(f"\nWebサーバーを起動しました: http://0.0.0.0:{PORT}")
    print(f"現在のモデル: {current_model}")
    print("Ctrl+Cで終了")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを終了します")
        server.shutdown()


if __name__ == '__main__':
    main()
