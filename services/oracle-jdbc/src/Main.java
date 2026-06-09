import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.Executors;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Tiny stateless HTTP gateway in front of ojdbc11.jar.
 *
 * Protocol:
 *   POST /merge   Content-Type: application/x-www-form-urlencoded
 *     fields: url, user, password, table_name, mk_date,
 *             sta_no1, sta_no2, sta_no3, t1_status,
 *             connect_timeout_ms, read_timeout_ms
 *   200 text/plain   key=value lines:
 *     rows_affected=N
 *     ora_code=NNN | (empty)
 *     error_message=...
 *
 *   GET /healthz   200 "ok"
 *
 * No connection pool: each request opens a fresh JDBC connection. The HIME-H-REAP
 * workload is single-digit events per minute, so per-call overhead (~100-500ms)
 * is dwarfed by Oracle-side cost. Keeps server stateless and crash-safe.
 */
public class Main {
    private static final Logger LOG = Logger.getLogger("oracle-jdbc");
    // UPCMPFLG (upstream-completion flag) is per-profile, configured in
    // profiles.yaml::oracle.upcmpflg. When the /merge form includes
    // "upcmpflg_value=N", the INSERT carries UPCMPFLG=N. When the form omits
    // it entirely, the INSERT does not touch the UPCMPFLG column (DB default
    // / NULL applies). Existing rows are never updated -- keeps exactly-once
    // semantics on re-publish.
    private static final String MERGE_SQL_WITHOUT_UPCMPFLG =
        "MERGE INTO %s t " +
        "USING (SELECT ? AS MK_DATE, ? AS STA_NO1, ? AS STA_NO2, " +
        "? AS STA_NO3, ? AS T1_STATUS FROM dual) s " +
        "ON (t.MK_DATE = s.MK_DATE AND t.STA_NO1 = s.STA_NO1 " +
        "AND t.STA_NO2 = s.STA_NO2 AND t.STA_NO3 = s.STA_NO3 " +
        "AND t.T1_STATUS = s.T1_STATUS) " +
        "WHEN NOT MATCHED THEN " +
        "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS) " +
        "VALUES (s.MK_DATE, s.STA_NO1, s.STA_NO2, s.STA_NO3, s.T1_STATUS)";
    private static final String MERGE_SQL_WITH_UPCMPFLG =
        "MERGE INTO %s t " +
        "USING (SELECT ? AS MK_DATE, ? AS STA_NO1, ? AS STA_NO2, " +
        "? AS STA_NO3, ? AS T1_STATUS, ? AS UPCMPFLG FROM dual) s " +
        "ON (t.MK_DATE = s.MK_DATE AND t.STA_NO1 = s.STA_NO1 " +
        "AND t.STA_NO2 = s.STA_NO2 AND t.STA_NO3 = s.STA_NO3 " +
        "AND t.T1_STATUS = s.T1_STATUS) " +
        "WHEN NOT MATCHED THEN " +
        "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS, UPCMPFLG) " +
        "VALUES (s.MK_DATE, s.STA_NO1, s.STA_NO2, s.STA_NO3, s.T1_STATUS, s.UPCMPFLG)";

    public static void main(String[] args) throws IOException {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8086"));
        HttpServer server = HttpServer.create(new InetSocketAddress("0.0.0.0", port), 16);
        server.createContext("/healthz", Main::healthz);
        server.createContext("/merge", Main::merge);
        server.createContext("/cleanup_range", Main::cleanupRange);
        server.createContext("/select_range", Main::selectRange);
        server.createContext("/select_recent", Main::selectRecent);
        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        LOG.info("oracle-jdbc listening on 0.0.0.0:" + port);
    }

    private static void healthz(HttpExchange ex) throws IOException {
        byte[] body = "ok\n".getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "text/plain; charset=utf-8");
        ex.sendResponseHeaders(200, body.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(body);
        }
    }

    private static void merge(HttpExchange ex) throws IOException {
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            sendPlain(ex, 405, "error_message=method not allowed\n");
            return;
        }
        Map<String, String> form;
        try (InputStream is = ex.getRequestBody()) {
            String raw = new String(is.readAllBytes(), StandardCharsets.UTF_8);
            form = parseForm(raw);
        }
        for (String required : new String[]{
            "url", "user", "password", "table_name", "mk_date",
            "sta_no1", "sta_no2", "sta_no3", "t1_status"
        }) {
            if (!form.containsKey(required)) {
                sendPlain(ex, 400, "error_message=missing field: " + required + "\n");
                return;
            }
        }

        String url = form.get("url");
        String user = form.get("user");
        String password = form.get("password");
        String tableName = form.get("table_name");
        if (!isSafeTableName(tableName)) {
            sendPlain(ex, 400, "error_message=invalid table_name\n");
            return;
        }
        int t1Status;
        int connectTimeoutMs;
        int readTimeoutMs;
        Integer upcmpflgValue = null;
        try {
            t1Status = Integer.parseInt(form.get("t1_status"));
            connectTimeoutMs = Integer.parseInt(form.getOrDefault("connect_timeout_ms", "10000"));
            readTimeoutMs = Integer.parseInt(form.getOrDefault("read_timeout_ms", "30000"));
            if (form.containsKey("upcmpflg_value")) {
                upcmpflgValue = Integer.parseInt(form.get("upcmpflg_value"));
            }
        } catch (NumberFormatException nfe) {
            sendPlain(ex, 400, "error_message=invalid integer field: " + nfe.getMessage() + "\n");
            return;
        }

        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("oracle.net.CONNECT_TIMEOUT", Integer.toString(connectTimeoutMs));
        props.setProperty("oracle.jdbc.ReadTimeout", Integer.toString(readTimeoutMs));

        int rowsAffected = 0;
        Integer oraCode = null;
        String errorMessage = "";

        String mergeTemplate = (upcmpflgValue == null)
            ? MERGE_SQL_WITHOUT_UPCMPFLG
            : MERGE_SQL_WITH_UPCMPFLG;
        try (Connection conn = DriverManager.getConnection(url, props)) {
            conn.setAutoCommit(false);
            try (PreparedStatement stmt = conn.prepareStatement(
                    String.format(mergeTemplate, tableName))) {
                stmt.setString(1, form.get("mk_date"));
                stmt.setString(2, form.get("sta_no1"));
                stmt.setString(3, form.get("sta_no2"));
                stmt.setString(4, form.get("sta_no3"));
                stmt.setInt(5, t1Status);
                if (upcmpflgValue != null) {
                    stmt.setInt(6, upcmpflgValue);
                }
                rowsAffected = stmt.executeUpdate();
            }
            conn.commit();
        } catch (SQLException sqlEx) {
            oraCode = sqlEx.getErrorCode() != 0 ? sqlEx.getErrorCode() : null;
            errorMessage = sanitizeOneLine(sqlEx.getMessage());
            LOG.log(Level.WARNING, "merge_failed ora_code=" + oraCode + " msg=" + errorMessage);
        } catch (Exception other) {
            errorMessage = sanitizeOneLine(other.getClass().getSimpleName() + ": " + other.getMessage());
            LOG.log(Level.WARNING, "merge_internal_error: " + errorMessage);
        }

        StringBuilder sb = new StringBuilder();
        sb.append("rows_affected=").append(rowsAffected).append('\n');
        sb.append("ora_code=").append(oraCode == null ? "" : oraCode.toString()).append('\n');
        sb.append("error_message=").append(errorMessage).append('\n');
        sendPlain(ex, 200, sb.toString());
    }

    /**
     * One-shot maintenance endpoint: deletes rows that were captured under a
     * non-production SSID and accidentally flushed (see drop-policy story in
     * services/bridge/src/main.py). Strictly parameterized; the only thing
     * the caller controls is the (station triple, mk_date range) window.
     *
     * Hard-coded guard: never deletes rows whose MK_DATE starts with '2099'
     * (those are sentinel rows created by verify_himereap_oracle.sh and must
     * survive cleanup).
     */
    private static void cleanupRange(HttpExchange ex) throws IOException {
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            sendPlain(ex, 405, "error_message=method not allowed\n");
            return;
        }
        Map<String, String> form;
        try (InputStream is = ex.getRequestBody()) {
            form = parseForm(new String(is.readAllBytes(), StandardCharsets.UTF_8));
        }
        for (String required : new String[]{
            "url", "user", "password", "table_name",
            "sta_no1", "sta_no2", "sta_no3",
            "mk_date_from", "mk_date_to"
        }) {
            if (!form.containsKey(required)) {
                sendPlain(ex, 400, "error_message=missing field: " + required + "\n");
                return;
            }
        }
        String tableName = form.get("table_name");
        if (!isSafeTableName(tableName)) {
            sendPlain(ex, 400, "error_message=invalid table_name\n");
            return;
        }

        Properties props = new Properties();
        props.setProperty("user", form.get("user"));
        props.setProperty("password", form.get("password"));
        props.setProperty("oracle.net.CONNECT_TIMEOUT",
            form.getOrDefault("connect_timeout_ms", "10000"));
        props.setProperty("oracle.jdbc.ReadTimeout",
            form.getOrDefault("read_timeout_ms", "30000"));

        String sql = "DELETE FROM " + tableName +
            " WHERE STA_NO1 = ? AND STA_NO2 = ? AND STA_NO3 = ?" +
            " AND MK_DATE BETWEEN ? AND ?" +
            " AND MK_DATE NOT LIKE '2099%'";

        int rowsDeleted = 0;
        Integer oraCode = null;
        String errorMessage = "";

        try (Connection conn = DriverManager.getConnection(form.get("url"), props)) {
            conn.setAutoCommit(false);
            try (PreparedStatement stmt = conn.prepareStatement(sql)) {
                stmt.setString(1, form.get("sta_no1"));
                stmt.setString(2, form.get("sta_no2"));
                stmt.setString(3, form.get("sta_no3"));
                stmt.setString(4, form.get("mk_date_from"));
                stmt.setString(5, form.get("mk_date_to"));
                rowsDeleted = stmt.executeUpdate();
            }
            conn.commit();
        } catch (SQLException sqlEx) {
            oraCode = sqlEx.getErrorCode() != 0 ? sqlEx.getErrorCode() : null;
            errorMessage = sanitizeOneLine(sqlEx.getMessage());
            LOG.log(Level.WARNING, "cleanup_failed ora_code=" + oraCode + " msg=" + errorMessage);
        } catch (Exception other) {
            errorMessage = sanitizeOneLine(other.getClass().getSimpleName() + ": " + other.getMessage());
            LOG.log(Level.WARNING, "cleanup_internal_error: " + errorMessage);
        }

        StringBuilder sb = new StringBuilder();
        sb.append("rows_deleted=").append(rowsDeleted).append('\n');
        sb.append("ora_code=").append(oraCode == null ? "" : oraCode.toString()).append('\n');
        sb.append("error_message=").append(errorMessage).append('\n');
        sendPlain(ex, 200, sb.toString());
    }

    /**
     * Read-only window inspector. Used by scripts/live_himereap_run.sh to
     * confirm that real ENTER/EXIT events captured during a test run made it
     * into HHC001. Same key shape as /cleanup_range; never modifies data.
     *
     * Response (text/plain):
     *   count=<N>
     *   ora_code=<N|empty>
     *   error_message=<one-line>
     *   row=<MK_DATE>,<STA_NO1>,<STA_NO2>,<STA_NO3>,<T1_STATUS>,<UPCMPFLG>
     *   row=...
     *
     * LIMIT 100 to bound the response.
     */
    private static void selectRange(HttpExchange ex) throws IOException {
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            sendPlain(ex, 405, "error_message=method not allowed\n");
            return;
        }
        Map<String, String> form;
        try (InputStream is = ex.getRequestBody()) {
            form = parseForm(new String(is.readAllBytes(), StandardCharsets.UTF_8));
        }
        for (String required : new String[]{
            "url", "user", "password", "table_name",
            "sta_no1", "sta_no2", "sta_no3",
            "mk_date_from", "mk_date_to"
        }) {
            if (!form.containsKey(required)) {
                sendPlain(ex, 400, "error_message=missing field: " + required + "\n");
                return;
            }
        }
        String tableName = form.get("table_name");
        if (!isSafeTableName(tableName)) {
            sendPlain(ex, 400, "error_message=invalid table_name\n");
            return;
        }

        Properties props = new Properties();
        props.setProperty("user", form.get("user"));
        props.setProperty("password", form.get("password"));
        props.setProperty("oracle.net.CONNECT_TIMEOUT",
            form.getOrDefault("connect_timeout_ms", "10000"));
        props.setProperty("oracle.jdbc.ReadTimeout",
            form.getOrDefault("read_timeout_ms", "30000"));

        String sql =
            "SELECT MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS, UPCMPFLG FROM " + tableName +
            " WHERE STA_NO1 = ? AND STA_NO2 = ? AND STA_NO3 = ?" +
            " AND MK_DATE BETWEEN ? AND ?" +
            " AND MK_DATE NOT LIKE '2099%'" +
            " ORDER BY MK_DATE FETCH FIRST 100 ROWS ONLY";

        int count = 0;
        Integer oraCode = null;
        String errorMessage = "";
        StringBuilder rows = new StringBuilder();

        try (Connection conn = DriverManager.getConnection(form.get("url"), props);
             PreparedStatement stmt = conn.prepareStatement(sql)) {
            stmt.setString(1, form.get("sta_no1"));
            stmt.setString(2, form.get("sta_no2"));
            stmt.setString(3, form.get("sta_no3"));
            stmt.setString(4, form.get("mk_date_from"));
            stmt.setString(5, form.get("mk_date_to"));
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    rows.append("row=")
                        .append(rs.getString(1)).append(',')
                        .append(rs.getString(2)).append(',')
                        .append(rs.getString(3)).append(',')
                        .append(rs.getString(4)).append(',')
                        .append(rs.getInt(5)).append(',')
                        .append(rs.getInt(6)).append('\n');
                    count++;
                }
            }
        } catch (SQLException sqlEx) {
            oraCode = sqlEx.getErrorCode() != 0 ? sqlEx.getErrorCode() : null;
            errorMessage = sanitizeOneLine(sqlEx.getMessage());
        } catch (Exception other) {
            errorMessage = sanitizeOneLine(other.getClass().getSimpleName() + ": " + other.getMessage());
        }

        StringBuilder sb = new StringBuilder();
        sb.append("count=").append(count).append('\n');
        sb.append("ora_code=").append(oraCode == null ? "" : oraCode.toString()).append('\n');
        sb.append("error_message=").append(errorMessage).append('\n');
        sb.append(rows);
        sendPlain(ex, 200, sb.toString());
    }

    /**
     * Latest-N rows for a station triple, newest first. Unlike /select_range
     * this needs no time window: it answers "what are the most recent records
     * that actually landed in Oracle?" -- the question the Desktop "直近N件"
     * tool asks to prove writes are real.
     *
     * POST fields: url, user, password, table_name, sta_no1, sta_no2, sta_no3
     *   optional: limit (default 30, capped at 200),
     *             connect_timeout_ms, read_timeout_ms
     * 2099% sentinel rows (verify_himereap_oracle smoke MERGEs) are excluded.
     */
    private static void selectRecent(HttpExchange ex) throws IOException {
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            sendPlain(ex, 405, "error_message=method not allowed\n");
            return;
        }
        Map<String, String> form;
        try (InputStream is = ex.getRequestBody()) {
            form = parseForm(new String(is.readAllBytes(), StandardCharsets.UTF_8));
        }
        for (String required : new String[]{
            "url", "user", "password", "table_name",
            "sta_no1", "sta_no2", "sta_no3"
        }) {
            if (!form.containsKey(required)) {
                sendPlain(ex, 400, "error_message=missing field: " + required + "\n");
                return;
            }
        }
        String tableName = form.get("table_name");
        if (!isSafeTableName(tableName)) {
            sendPlain(ex, 400, "error_message=invalid table_name\n");
            return;
        }

        // FETCH FIRST N cannot reliably be bound across Oracle versions, so the
        // limit is validated as a small integer and inlined. Default 30; capped
        // at 200 to bound the response size.
        int limit = 30;
        try {
            limit = Integer.parseInt(form.getOrDefault("limit", "30").trim());
        } catch (NumberFormatException nfe) {
            sendPlain(ex, 400, "error_message=invalid limit\n");
            return;
        }
        if (limit < 1) limit = 1;
        if (limit > 200) limit = 200;

        Properties props = new Properties();
        props.setProperty("user", form.get("user"));
        props.setProperty("password", form.get("password"));
        props.setProperty("oracle.net.CONNECT_TIMEOUT",
            form.getOrDefault("connect_timeout_ms", "10000"));
        props.setProperty("oracle.jdbc.ReadTimeout",
            form.getOrDefault("read_timeout_ms", "30000"));

        String sql =
            "SELECT MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS, UPCMPFLG FROM " + tableName +
            " WHERE STA_NO1 = ? AND STA_NO2 = ? AND STA_NO3 = ?" +
            " AND MK_DATE NOT LIKE '2099%'" +
            " ORDER BY MK_DATE DESC FETCH FIRST " + limit + " ROWS ONLY";

        int count = 0;
        Integer oraCode = null;
        String errorMessage = "";
        StringBuilder rows = new StringBuilder();

        try (Connection conn = DriverManager.getConnection(form.get("url"), props);
             PreparedStatement stmt = conn.prepareStatement(sql)) {
            stmt.setString(1, form.get("sta_no1"));
            stmt.setString(2, form.get("sta_no2"));
            stmt.setString(3, form.get("sta_no3"));
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    rows.append("row=")
                        .append(rs.getString(1)).append(',')
                        .append(rs.getString(2)).append(',')
                        .append(rs.getString(3)).append(',')
                        .append(rs.getString(4)).append(',')
                        .append(rs.getInt(5)).append(',')
                        .append(rs.getInt(6)).append('\n');
                    count++;
                }
            }
        } catch (SQLException sqlEx) {
            oraCode = sqlEx.getErrorCode() != 0 ? sqlEx.getErrorCode() : null;
            errorMessage = sanitizeOneLine(sqlEx.getMessage());
        } catch (Exception other) {
            errorMessage = sanitizeOneLine(other.getClass().getSimpleName() + ": " + other.getMessage());
        }

        StringBuilder sb = new StringBuilder();
        sb.append("count=").append(count).append('\n');
        sb.append("ora_code=").append(oraCode == null ? "" : oraCode.toString()).append('\n');
        sb.append("error_message=").append(errorMessage).append('\n');
        sb.append(rows);
        sendPlain(ex, 200, sb.toString());
    }

    private static void sendPlain(HttpExchange ex, int code, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "text/plain; charset=utf-8");
        ex.sendResponseHeaders(code, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static Map<String, String> parseForm(String body) {
        Map<String, String> out = new HashMap<>();
        if (body == null || body.isEmpty()) return out;
        for (String pair : body.split("&")) {
            int eq = pair.indexOf('=');
            if (eq < 0) continue;
            String k = URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8);
            String v = URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8);
            out.put(k, v);
        }
        return out;
    }

    private static boolean isSafeTableName(String s) {
        // Guard against SQL injection through the f-string format above:
        // table_name comes from validated config but we still belt-and-braces.
        if (s == null || s.isEmpty() || s.length() > 64) return false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (!(Character.isLetterOrDigit(c) || c == '_' || c == '$' || c == '#')) {
                return false;
            }
        }
        return true;
    }

    private static String sanitizeOneLine(String s) {
        if (s == null) return "";
        return s.replace('\r', ' ').replace('\n', ' ').trim();
    }
}
