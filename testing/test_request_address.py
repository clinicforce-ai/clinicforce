"""Trust only the verified right-hand CloudFront/ALB suffix, never viewer prefixes."""

import os
import unittest
from unittest.mock import patch

from starlette.requests import Request

from app.core.request_address import request_address


def request(peer="10.20.1.5", header="198.51.100.23, 203.0.113.8", *, extra_headers=None):
    return Request({"type": "http", "method": "POST", "path": "/", "headers":
                    ([(b"x-forwarded-for", header.encode())] if header is not None else []) + (extra_headers or []),
                    "client": (peer, 54321) if peer is not None else None})


class RequestAddressTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TRUSTED_PROXY_CIDRS": "10.20.0.0/16,2001:db8:100::/48", "TRUSTED_CLOUDFRONT_ALB_HOPS": "2"})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_trusted_two_hops_ignore_spoofed_prefixes(self):
        for prefix in ("", "192.0.2.99, ", "not-an-ip, 192.0.2.1, "):
            with self.subTest(prefix=prefix):
                self.assertEqual(request_address(request(header=prefix + "198.51.100.23, 203.0.113.8")), "198.51.100.23")
        self.assertEqual(request_address(request(header="198.51.100.24, 203.0.113.8")), "198.51.100.24")

    def test_untrusted_peer_or_missing_explicit_configuration_ignores_headers(self):
        self.assertEqual(request_address(request(peer="192.0.2.5")), "192.0.2.5")
        self.assertEqual(request_address(request(peer="127.0.0.1")), "127.0.0.1")
        for environment in ({"TRUSTED_PROXY_CIDRS": ""}, {"TRUSTED_PROXY_CIDRS": "10.20.0.0/16,invalid"}, {"TRUSTED_PROXY_CIDRS": "0.0.0.0/0"},
                            {"TRUSTED_CLOUDFRONT_ALB_HOPS": ""}, {"TRUSTED_CLOUDFRONT_ALB_HOPS": "1"}, {"TRUSTED_CLOUDFRONT_ALB_HOPS": "3"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment):
                self.assertEqual(request_address(request()), "10.20.1.5")

    def test_ipv6_canonicalization_and_alb_optional_port(self):
        self.assertEqual(request_address(request(header="2001:0DB8::0023, 203.0.113.8")), "2001:db8::23")
        self.assertEqual(request_address(request(peer="2001:db8:100::5", header="2001:db8::23, 2001:db8::8")), "2001:db8::23")
        self.assertEqual(request_address(request(header="198.51.100.23, 203.0.113.8:443")), "198.51.100.23")
        self.assertEqual(request_address(request(header="198.51.100.23, [2001:db8::8]:443")), "198.51.100.23")
        self.assertEqual(request_address(request(peer="::ffff:10.20.1.5", header="::ffff:198.51.100.23, 203.0.113.8")), "198.51.100.23")

    def test_incomplete_malformed_or_duplicate_proxy_suffix_falls_back(self):
        for value in (None, "", "198.51.100.23", "198.51.100.23,", ",203.0.113.8", "bad-client,203.0.113.8", "198.51.100.23,bad-edge",
                      "198.51.100.23,203.0.113.8:99999", "198.51.100.23,[2001:db8::8]:0", "fe80::1%eth0,203.0.113.8", "x" * 4097):
            with self.subTest(value=value):
                self.assertEqual(request_address(request(header=value)), "10.20.1.5")
        self.assertEqual(request_address(request(extra_headers=[(b"x-forwarded-for", b"192.0.2.99")])), "10.20.1.5")
        self.assertEqual(request_address(request(peer=None)), "unknown")
        self.assertEqual(request_address(request(peer="not-a-peer")), "unknown")


if __name__ == "__main__":
    unittest.main()
