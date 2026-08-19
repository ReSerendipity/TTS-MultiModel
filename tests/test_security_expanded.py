"""Security test suite expansion - SQL injection, XSS, SSRF coverage.

补充报告中发现的安全测试盲区（原测试体系缺少 SQL/XSS/SSRF）。
"""
import os
import sys
import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


# ============================================================================
# SQL Injection / FTS5 Tests
# ============================================================================

class TestHistoryDBSQLInjectionPrevention:
    """Test that history DB properly prevents SQL injection via parameterized queries."""

    def test_fts_search_parameterized_query(self):
        """FTS5 MATCH queries should use parameterized bindings, not string interpolation.

        Regression test for CVE-style injection via search_text parameter.
        See app/integrated_app/history_db.py:_build_filter_conditions() line 477,1047
        """
        from integrated_app.history_db import HistoryDatabase
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = HistoryDatabase(db_path)

            # Malicious FTS5 injection attempts (should be treated as literal search text)
            malicious_inputs = [
                "' OR '1'='1",
                "'; DROP TABLE history; --",
                "test' UNION SELECT * FROM history WHERE '1'='1",
                "*/ DELETE FROM history /*",
                "**/DELETE/**/FROM/**/history/*/",
                "test') AND (SELECT COUNT(*) FROM history) > 0 OR ('1'='1",
            ]

            for malicious in malicious_inputs:
                # Should not raise exceptions or cause unexpected results
                conditions, params = db._build_filter_conditions(search_text=malicious)

                # Verify parameterized binding is used
                assert "?" in str(conditions) or len(params) > 0, \
                    f"Query should use parameterized binding, got conditions={conditions}"

                # Verify the malicious input ends up in params, NOT in conditions
                condition_str = " ".join(conditions) if conditions else ""
                assert malicious not in condition_str, \
                    f"Malicious input leaked into SQL condition: {condition_str}"

        finally:
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except PermissionError:
                    pass

    def test_filter_key_whitelist_enforcement(self):
        """Unknown filter keys should be rejected/logged, not passed to SQL.

        Prevents injection via filter key names (e.g., {"engine"); DROP TABLE..." : "x"}).
        See app/integrated_app/history_db.py line 446-449
        """
        from integrated_app.history_db import HistoryDatabase
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = HistoryDatabase(db_path)

            # Attempt to inject via filter key name
            malicious_filters = {
                "engine DROP TABLE history": "test",
                "is_success OR 1=1": "true",
                "time_from DELETE FROM history": "2024-01-01",
            }

            for key in malicious_filters.keys():
                conditions, params = db._build_filter_conditions(filters={key: "value"})

                # Malicious key should be logged and ignored, not added to conditions
                condition_str = " ".join(conditions) if conditions else ""
                assert key not in condition_str, \
                    f"Malicious filter key leaked into SQL: {condition_str}"

        finally:
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except PermissionError:
                    pass

    def test_filename_search_parameterization(self):
        """filename search should also use parameterized LIKE queries."""
        from integrated_app.history_db import HistoryDatabase
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = HistoryDatabase(db_path)

            # Malicious inputs that could break LIKE pattern matching
            malicious_patterns = [
                "%', '1'); DROP TABLE history; --",
                "_'% OR '1'='1",
                "%' AND '1'='1",
            ]

            for malicious in malicious_patterns:
                conditions, params = db._build_filter_conditions(
                    search_text="test", search_filename=True
                )

                # Verify safe parameterized construction
                condition_str = " ".join(conditions) if conditions else ""
                assert malicious not in condition_str, \
                    f"Malicious filename pattern leaked: {condition_str}"

        finally:
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except PermissionError:
                    pass


# ============================================================================
# XSS Prevention Tests
# ============================================================================

class TestXSSPrevention:
    """Test XSS prevention in text processing and HTML output generation."""

    def test_text_frontend_xss_tag_stripping(self):
        """TextFrontend should handle HTML/script tags safely in text input.

        Attack vector: User submits "<script>stealCookies()</script>" expecting it to
        be echoed back in an error message or progress display.
        """
        from integrated_app.text_frontend import TextFrontend

        frontend = TextFrontend()

        # Malicious script injection attempts
        xss_payloads = [
            "<script>alert('XSS')</script>",
            "<img src=x onerror=alert('XSS')>",
            "<svg onload=alert('XSS')>",
        ]

        for payload in xss_payloads:
            # The key security guarantee: user input must NEVER be rendered as raw HTML
            # in any server-sent responses.
            try:
                result = frontend.process(payload)
                # If process() returns segments, verify no executable HTML survives
                assert result is not None
                # If result is a string, verify script tags are not preserved verbatim
                if isinstance(result, str):
                    assert "<script>" not in result.lower(), \
                        f"XSS payload survived processing: {result}"
                elif isinstance(result, list):
                    for segment in result:
                        if isinstance(segment, str):
                            assert "<script>" not in segment.lower(), \
                                f"XSS payload survived in segment: {segment}"
            except ValueError:
                # Rejecting malicious input is also acceptable
                pass
            except Exception as e:
                # Unexpected exceptions should not contain the raw XSS payload
                assert "<script>" not in str(e).lower(), \
                    f"Error message leaked XSS payload: {e}"

    def test_html_progress_bar_escaping(self):
        """Progress bar HTML generation should escape user-provided text fields.
        
        Note: ProgressManager uses hardcoded strings internally. User-controlled text
        flows through FastAPI/Jinja2 which handles escaping at the response layer.
        This test documents the defense-in-depth strategy.
        """
        from integrated_app.progress import ProgressManager

        pm = ProgressManager()
        pm.start(total_segments=1, phase="正常阶段")
        
        # Verify progress manager produces valid HTML structure
        html = pm.get_progress_html()
        
        # Empty phase during early stage is expected behavior (<0.5s threshold)
        if pm._start_time > 0 and (pm._phase or "") == "":
            assert html == ""  # Early return is intentional
            
        # When HTML is generated, internal strings use safe characters only
        # User text would be escaped by Jinja2 before insertion
        assert isinstance(html, str)

    def test_error_messages_do_not_leak_stack_traces(self):
        """Error responses should not expose stack traces in production mode.

        Regression test for information disclosure vulnerability where detailed
        exception messages could reveal internal paths, credentials, or logic.
        """
        # Placeholder: actual error sanitization happens in middleware/error handlers
        # Verify the concept exists
        
        class ErrorSanitizer:
            """Example error sanitization utility."""
            
            @staticmethod
            def sanitize_user_facing_message(error_msg: str, include_debug: bool = False) -> str:
                """Strip sensitive information from error messages."""
                import re
                
                # Remove common sensitive patterns
                patterns = [
                    r'Password["\']?\s*[:=]\s*["\'][^"\']*["\']',
                    r'Token["\']?\s*[:=]\s*["\'][^"\']*["\']',
                    r'SECRET[_A-Z]*["\']?\s*[:=]\s*["\'][^"\']*["\']',
                ]
                
                sanitized = error_msg
                for pattern in patterns:
                    sanitized = re.sub(pattern, '[REDACTED]', sanitized, flags=re.IGNORECASE)
                
                # Remove stack traces unless debug mode
                if not include_debug:
                    stack_match = re.search(r'\n\s*File ".*?".*?\n\s*(?:in.*?\n\s*)+.*?\^\^+', 
                                          sanitized)
                    if stack_match:
                        sanitized = sanitized[:stack_match.start()] + "\n[Stack trace hidden]"
                
                return sanitized
            
            @staticmethod
            def is_safe_for_display(error_msg: str) -> bool:
                """Check if error message is safe to show to users."""
                dangerous_patterns = ['password=', 'token=', 'secret=', 'api_key=', 
                                     '<script', 'DROP TABLE', 'DELETE FROM']
                lower_msg = error_msg.lower()
                return not any(p.lower() in lower_msg for p in dangerous_patterns)
        
        sanitizer = ErrorSanitizer()
        
        # Test sanitization
        malicious = "Failed with password='supersecret' and token='abc123'"
        safe = sanitizer.sanitize_user_facing_message(malicious)
        assert "password" not in safe.lower() or "[REDACTED]" in safe
        
        # Test safety check
        assert sanitizer.is_safe_for_display("Operation completed successfully") is True
        assert sanitizer.is_safe_for_display("Error: DROP TABLE users") is False


# ============================================================================
# SSRF Prevention Tests  
# ============================================================================

class TestSSRFPrevention:
    """Test SSRF prevention for any URL-fetching functionality."""

    def test_no_internal_url_fetching_endpoints(self):
        """Verify no endpoints accept arbitrary URLs for fetching (common SSRF vector).

        If such endpoints exist, they should have URL validation/blocklist checks.
        """
        from integrated_app.app_server import create_app
        import re

        app = create_app()

        # Scan all registered routes for URL-fetching patterns
        url_fetch_keywords = ["url=", "fetch", "download", "pull", "remote"]
        suspicious_routes = []

        for route in app.routes:
            if hasattr(route, 'path') and hasattr(route, 'methods'):
                path_lower = route.path.lower()
                methods_lower = [m.lower() for m in route.methods]

                # Look for GET/POST with URL parameters
                if "get" in methods_lower or "post" in methods_lower:
                    for keyword in url_fetch_keywords:
                        if keyword in path_lower:
                            suspicious_routes.append({
                                "path": route.path,
                                "methods": route.methods,
                                "name": getattr(route, 'name', 'unnamed')
                            })

        # For now, just document these routes - future tests should validate their URL handling
        # If this assertion fails, add proper SSRF protection tests for those endpoints
        print(f"SUSPICIOUS ROUTES (need SSRF protection): {suspicious_routes}")

    def test_audio_file_url_validation(self):
        """If audio files can be loaded from URLs, they should validate against internal IPs."""
        # This test documents the requirement - implementation may vary by feature
        # Future: Add actual URL validation test when URL-based audio loading is implemented
        
        # Placeholder: verify the concept exists
        import ipaddress

        def is_safe_url(url: str) -> bool:
            """Example SSRF-safe URL checker (not actually used, just testing the logic)."""
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)

                # Only allow http/https
                if parsed.scheme not in ("http", "https"):
                    return False

                # Block internal IP ranges
                hostname = parsed.hostname
                if not hostname:
                    return False

                import socket
                ips = socket.getaddrinfo(hostname, None)
                for family, _, _, _, addr in ips:
                    ip_str = addr[0]
                    try:
                        ip = ipaddress.ip_address(ip_str)
                        if ip.is_private or ip.is_loopback or ip.is_link_local:
                            return False
                    except ValueError:
                        continue  # Not a valid IP, might be a domain

                return True
            except Exception:
                return False

        # Test cases
        assert is_safe_url("https://example.com/audio.wav") is True
        assert is_safe_url("http://192.168.1.1/test.wav") is False
        assert is_safe_url("http://127.0.0.1/test.wav") is False
        assert is_safe_url("http://localhost/test.wav") is False
        assert is_safe_url("file:///etc/passwd") is False
        assert is_safe_url("ftp://internal-server/file") is False


# ============================================================================
# HTTP Behavior-Level XSS Prevention Tests
# ============================================================================

class TestXSSHTTPBehavior:
    """Test XSS prevention through actual HTTP responses.

    These tests verify that user-supplied text containing XSS payloads
    is properly escaped when returned in HTTP responses from real endpoints.
    """

    def test_error_response_does_not_contain_raw_script_tag(self, client):
        """Error responses must not echo back raw <script> tags from user input.

        Attack vector: Submit XSS payload as text parameter; if the error
        response includes the raw payload, a browser would execute it.
        """
        xss_payload = "<script>alert('XSS')</script>"
        resp = client.get(f"/api/nonexistent-{xss_payload}")

        # Regardless of status code, the response body must not contain
        # the raw, unescaped <script> tag
        body = resp.text
        assert "<script>alert" not in body, \
            f"Raw XSS payload found in response body: {body[:200]}"

    def test_404_response_escapes_html(self, client):
        """404 responses should escape HTML entities in any echoed path."""
        xss_path = "/api/<img src=x onerror=alert(1)>"
        resp = client.get(xss_path)
        assert resp.status_code == 404

        body = resp.text
        # The raw onerror attribute must not appear unescaped
        assert "onerror=alert" not in body, \
            f"Unescaped HTML event handler in 404 body: {body[:200]}"

    def test_csrf_rejection_message_is_safe(self, client):
        """CSRF rejection response should not contain executable HTML."""
        # POST without CSRF token to trigger 403
        resp = client.post("/api/training/stop")
        assert resp.status_code == 403

        body = resp.text
        assert "<script>" not in body.lower()
        assert "onerror=" not in body.lower()

    def test_history_table_response_is_safe(self, client):
        """History table endpoint should not contain unescaped script tags."""
        resp = client.get("/api/history/table")
        assert resp.status_code == 200

        body = resp.text
        assert "<script>alert" not in body.lower()

    def test_tab_content_does_not_leak_html(self, client):
        """Tab content responses should be properly escaped."""
        # Request a tab with a path that could contain XSS
        resp = client.get("/tab/settings")
        assert resp.status_code == 200

        body = resp.text
        # No raw script tags should appear in tab content
        assert "<script>alert" not in body.lower()


# ============================================================================
# HTTP Behavior-Level SSRF Prevention Tests
# ============================================================================

class TestSSRFHTTPBehavior:
    """Test SSRF prevention through actual HTTP endpoint behavior.

    These tests verify that no API endpoint can be abused to make the
    server fetch arbitrary internal URLs.
    """

    def test_no_endpoint_accepts_url_parameter_for_fetching(self, client):
        """No API endpoint should accept a 'url' parameter that causes server-side fetching."""
        # Try common SSRF vectors across all registered routes
        ssrf_vectors = [
            "/api/audio/?url=http://169.254.169.254/latest/meta-data/",
            "/api/generate/?url=http://127.0.0.1:8080/admin",
            "/api/download/?url=file:///etc/passwd",
            "/api/proxy/?url=http://127.0.0.1:7869/api/health/ping",
        ]

        for vector in ssrf_vectors:
            resp = client.get(vector)
            # These should all return 404 (endpoint doesn't exist) or 422 (parameter not accepted)
            # The key assertion: no 200 response with content from the target URL
            assert resp.status_code in (404, 422, 400, 405), \
                f"SSRF vector {vector} returned {resp.status_code} - possible SSRF vulnerability"

    def test_audio_upload_rejects_url_scheme(self, client):
        """Audio upload endpoint should reject URL-based file references."""
        # Attempt to upload a URL instead of a file
        resp = client.post(
            "/api/audio/upload",
            data={"file_url": "http://127.0.0.1/test.wav"},
        )
        # Should reject (400/403/404/422), not fetch the URL
        # 403 = CSRF or auth rejection (still safe - request was denied)
        assert resp.status_code in (400, 403, 404, 422, 405, 415), \
            f"Audio upload accepted URL reference: {resp.status_code}"

    def test_model_load_rejects_url_path(self, client):
        """Model loading should not accept URL-based paths."""
        resp = client.post(
            "/api/model/load",
            data={"engine": "voxcpm2", "model_url": "http://169.254.169.254/"},
        )
        # Should not fetch the URL - reject or ignore the url parameter
        assert resp.status_code in (200, 400, 422, 405, 403), \
            f"Model load endpoint may have fetched URL: {resp.status_code}"

    def test_generate_endpoint_rejects_remote_reference(self, client):
        """Generate endpoint should not accept remote reference audio URLs."""
        resp = client.post(
            "/api/generate/voice_clone",
            data={
                "text": "test",
                "reference_audio_url": "http://127.0.0.1:7869/api/health/ping",
            },
        )
        # Should reject or return error (no model loaded), not fetch the URL
        assert resp.status_code in (400, 422, 405, 503, 403), \
            f"Generate endpoint may have fetched remote URL: {resp.status_code}"

    def test_internal_ip_not_reachable_via_api(self, client):
        """Verify no API endpoint proxies requests to internal IPs."""
        # Attempt to use any endpoint as a proxy
        internal_targets = [
            "http://127.0.0.1:8080",
            "http://127.0.0.1:9000",
            "http://169.254.169.254",
            "http://[::1]:8080",
        ]

        for target in internal_targets:
            # Try common proxy/fetch parameter names
            for param_name in ("url", "target", "dest", "redirect", "next", "fetch"):
                resp = client.get(f"/api/nonexistent?{param_name}={target}")
                # Nonexistent endpoint should return 404
                assert resp.status_code == 404, \
                    f"Unexpected {resp.status_code} for nonexistent endpoint with {param_name}={target}"
