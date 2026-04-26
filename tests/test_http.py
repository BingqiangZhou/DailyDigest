"""Tests for core/http.py — error classification, timeout config, error labels."""

import socket
import pytest
from core.http import _classify_error, error_label, DEFAULT_TIMEOUT


class TestClassifyError:
    def test_socket_timeout(self):
        assert _classify_error(socket.timeout("timed out")) == -2

    def test_timeout_string(self):
        assert _classify_error(Exception("connection timed out")) == -2

    def test_timeout_in_type_name(self):
        assert _classify_error(TimeoutError("read timeout")) == -2

    def test_dns_getaddrinfo(self):
        assert _classify_error(Exception("getaddrinfo failed")) == -3

    def test_dns_nodename(self):
        assert _classify_error(Exception("nodename nor servname provided")) == -3

    def test_connection_refused(self):
        assert _classify_error(ConnectionRefusedError()) == -4

    def test_connection_reset(self):
        assert _classify_error(ConnectionResetError()) == -4

    def test_broken_pipe(self):
        assert _classify_error(BrokenPipeError()) == -4

    def test_connection_refused_string(self):
        assert _classify_error(Exception("Connection refused")) == -4

    def test_ssl_certificate(self):
        assert _classify_error(Exception("SSL: CERTIFICATE_VERIFY_FAILED")) == -5

    def test_ssl_hostname(self):
        assert _classify_error(Exception("hostname mismatch")) == -5

    def test_unknown_error(self):
        assert _classify_error(Exception("something weird")) == -1

    def test_generic_runtime_error(self):
        assert _classify_error(RuntimeError("unknown")) == -1


class TestErrorLabel:
    def test_timeout_label(self):
        assert error_label(-2) == "超时"

    def test_dns_label(self):
        assert error_label(-3) == "DNS解析失败"

    def test_connection_label(self):
        assert error_label(-4) == "连接被拒绝/重置"

    def test_ssl_label(self):
        assert error_label(-5) == "SSL错误"

    def test_unknown_label(self):
        assert error_label(-1) == "未知错误"

    def test_http_status_passthrough(self):
        assert error_label(404) == "HTTP 404"

    def test_http_304(self):
        assert error_label(304) == "HTTP 304"


class TestDefaultTimeout:
    def test_default_is_int(self):
        assert isinstance(DEFAULT_TIMEOUT, int)

    def test_default_at_least_15(self):
        assert DEFAULT_TIMEOUT >= 15
