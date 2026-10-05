#!/usr/bin/env python3
"""Verify SHL transport in memory; never print credentials, QR text or records.

Requires backend dependencies plus opencv-python for independent PNG decoding.
Use --synthetic only on staging. Otherwise supply SHARING_TEST_PATIENT_ID and
SHARING_TEST_PASSWORD in the environment for an explicitly authorized account.
This checks ClinicForce transport, not an external mobile application's import.
"""
import argparse
import base64
import json
import os
import secrets
import time
from urllib.parse import urlsplit, parse_qs
from xml.etree import ElementTree

import cv2
import httpx
import numpy as np
from jwcrypto import jwe, jwk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('environment', choices=['staging', 'production'])
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--require-diagnostics', action='store_true')
    args = parser.parse_args()
    if args.synthetic and args.environment != 'staging':
        parser.error('synthetic account creation is staging-only')
    base = 'https://' + ('staging' if args.environment == 'staging' else 'app') + '.clinicforce.ai'
    credentials = ({'patient_id': 'qa-shl-' + secrets.token_hex(8), 'password': secrets.token_urlsafe(32)}
                   if args.synthetic else {'patient_id': os.environ['SHARING_TEST_PATIENT_ID'],
                                           'password': os.environ['SHARING_TEST_PASSWORD']})
    with httpx.Client(base_url=base, timeout=130, headers={'Origin': base}) as client:
        def call(method, path, body, expected=200, sharing=False):
            response = client.request(method, path, json=body)
            # Exception messages must not contain the URL or response body.
            if response.status_code != expected:
                raise RuntimeError(f'Unexpected HTTP status {response.status_code}; expected {expected}')
            if sharing:
                trace = response.headers.get('X-ClinicForce-Request-ID')
                edge = response.headers.get('X-Amz-Cf-Id')
                if args.require_diagnostics:
                    assert trace and edge and response.headers.get('Cache-Control') == 'no-store'
                print(json.dumps({'check': 'sharing_http', 'status': response.status_code,
                                  'request_id': trace, 'cloudfront_request_id': edge}), flush=True)
            return response

        call('POST', '/api/patient/register' if args.synthetic else '/api/patient/login', credentials)
        try:
            if args.synthetic:
                call('PUT', '/api/patient/profile', {'given_name': 'Synthetic', 'family_name': 'QR Verification',
                     'birth_date': '1990-01-01', 'administrative_gender': 'unknown'})
                call('POST', '/api/patient/records', {'category': 'condition', 'name': 'Synthetic sharing check',
                     'details': 'Fictional transport test; not a clinical record.'})
            for mode in ['ecw_compat', 'standard']:
                pin = f'{secrets.randbelow(10000):04d}'
                body = {'share_mode': mode}
                if mode == 'standard':
                    body['passcode'] = pin
                result = call('POST', '/api/patient/generate-link', body, sharing=True).json()
                uri = result['shl_uri']
                png = cv2.imdecode(np.frombuffer(base64.b64decode(result['qr_code_base64']), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(png)
                assert decoded == uri, 'Independent QR PNG decode did not match the SHL'
                encoded = uri.split('shlink:/', 1)[1]
                payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
                assert payload['url'].startswith(base + '/manifest/')
                assert payload['v'] == 1 and 0 < payload['exp'] - time.time() <= 10800
                assert uri.startswith('shlink:/') == (mode == 'ecw_compat')
                assert (payload.get('flag') == 'P') == (mode == 'standard')
                request = {'recipient': 'ClinicForce authorized transport verification'}
                if mode == 'standard':
                    request['passcode'] = pin
                    call('POST', payload['url'], {**request, 'passcode': f'{(int(pin)+1)%10000:04d}'}, 401, True)
                for external in [False, True]:
                    manifest_request = {**request, 'embeddedLengthMax': 0} if external else request
                    file = call('POST', payload['url'], manifest_request, sharing=True).json()['files'][0]
                    assert file['contentType'] == 'application/fhir+json;fhirVersion=4.0.1'
                    if external:
                        assert 'embedded' not in file
                        # A separate client never forwards the portal cookie to S3.
                        with httpx.Client(timeout=30) as downloader:
                            download = downloader.get(file['location'])
                            if download.status_code != 200:
                                code = ElementTree.fromstring(download.content).findtext('Code')
                                print(json.dumps({'check': 'encrypted_object', 'status': download.status_code,
                                                  'error_code': code if code in {'InvalidArgument', 'InvalidRequest', 'AccessDenied', 'SignatureDoesNotMatch'} else 'other',
                                                  'signature_v4': 'X-Amz-Algorithm' in parse_qs(urlsplit(file['location']).query)}), flush=True)
                            assert download.status_code == 200, 'Encrypted-object download failed'
                            encrypted = download.text
                    else:
                        encrypted = file['embedded']
                    token = jwe.JWE()
                    token.deserialize(encrypted, key=jwk.JWK(kty='oct', k=payload['key']))
                    bundle = json.loads(token.payload)
                    assert bundle['resourceType'] == 'Bundle' and bundle['type'] == 'collection'
                    assert bundle['entry'][0]['resource']['resourceType'] == 'Patient' and len(bundle['entry']) > 1
                    if args.synthetic:
                        assert any(e['resource'].get('code', {}).get('text') == 'Synthetic sharing check' for e in bundle['entry'])
                print(json.dumps({'mode': mode, 'qr_png_and_embedded_and_external_decryption': 'PASS'}), flush=True)
            call('POST', '/manifest/' + secrets.token_urlsafe(32), {'recipient': 'Synthetic negative check'}, 404, True)
            call('POST', '/manifest/' + secrets.token_urlsafe(32), {}, 422, True)
        finally:
            call('POST', '/api/patient/logout', {}, 204)
    print('PASS: ClinicForce exchange; mobile chart import requires separate evidence.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # HTTP/library exceptions can contain capability URLs. Print only the class.
        print(f'FAIL: {type(error).__name__}; inspect privately without logging payloads.', flush=True)
        raise SystemExit(1) from None
