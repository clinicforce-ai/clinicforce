"""Import the official CMS October 2026 RVU ZIP into the public numeric TX cache."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.insurance.cms_pfs import DEFAULT_CACHE, SOURCE_URL, import_official_zip  # noqa: E402
from app.insurance.cms_locality import SOURCE_URL as LOCALITY_URL, import_official_zip as import_locality_zip  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('official_zip', type=Path, nargs='?')
    parser.add_argument('--output', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--zip-locality', type=Path,
                        help='Official CMS October 2026 ZIP-to-locality archive')
    parser.add_argument('--download', action='store_true',
                        help='Fetch both pinned official CMS releases before importing')
    args = parser.parse_args()
    if args.download and (args.official_zip or args.zip_locality):
        parser.error('--download cannot be combined with local ZIP paths')
    if not args.download and not args.official_zip:
        parser.error('provide the official RVU ZIP or use --download')
    if args.download:
        with tempfile.TemporaryDirectory() as temporary:
            rvu, locality = Path(temporary) / 'rvu26d.zip', Path(temporary) / 'zip-locality.zip'
            for url, destination in ((SOURCE_URL, rvu), (LOCALITY_URL, locality)):
                with urlopen(url, timeout=60) as response:
                    destination.write_bytes(response.read())
            cache = import_official_zip(rvu, args.output)
            localities = import_locality_zip(locality)
    else:
        cache = import_official_zip(args.official_zip, args.output)
        localities = import_locality_zip(args.zip_locality) if args.zip_locality else None
    print(f"{cache['version']}: {len(cache['gpci_tx'])} TX localities; "
          f"{len(cache['rates']['non_qp'])} supported non-QP rows; "
          f"SHA-256 {cache['source_sha256']}")
    if localities:
        print(f"{localities['version']}: {len(localities['zip5'])} TX service ZIPs; "
              f"SHA-256 {localities['source_sha256']}")


if __name__ == '__main__':
    main()
