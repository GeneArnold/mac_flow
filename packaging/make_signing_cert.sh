#!/usr/bin/env bash
# One-time: create a self-signed code-signing certificate for MacFlow.
#
# Why: macOS pins privacy grants (Accessibility, Input Monitoring) to an app's
# designated requirement. Ad-hoc signing makes that requirement the code hash,
# so every rebuild looks like a different program and silently loses its
# permissions — the app then fails with no error at all. A stable certificate
# makes the requirement reference the certificate, so grants persist.
#
# This is NOT a substitute for a Developer ID. It only helps on this machine;
# distributing to other people still needs real signing and notarization.
#
# Usage: bash packaging/make_signing_cert.sh
set -euo pipefail

NAME="${MACFLOW_SIGN_IDENTITY:-MacFlow Local Signing}"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if security find-identity -v -p codesigning | grep -q "$NAME"; then
    echo "Identity '$NAME' already exists. Nothing to do."
    exit 0
fi

echo "==> Generating key and self-signed certificate"
openssl req -x509 -newkey rsa:2048 -keyout "$WORK/key.pem" -out "$WORK/cert.pem" \
    -days 3650 -nodes -subj "/CN=$NAME" \
    -addext "extendedKeyUsage=codeSigning" \
    -addext "basicConstraints=critical,CA:false" \
    -addext "keyUsage=critical,digitalSignature" 2>/dev/null

# macOS's keychain importer rejects OpenSSL 3's default PKCS#12 algorithms and
# empty passwords, so use the legacy ones with a throwaway passphrase.
echo "==> Packaging as PKCS#12"
openssl pkcs12 -export -out "$WORK/identity.p12" \
    -inkey "$WORK/key.pem" -in "$WORK/cert.pem" -name "$NAME" \
    -passout pass:macflow \
    -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 2>/dev/null

echo "==> Importing into the login keychain"
security import "$WORK/identity.p12" -k "$KEYCHAIN" -P macflow -T /usr/bin/codesign -A

# Without an explicit code-signing trust setting the identity imports but is
# not usable — `find-identity -p codesigning` reports zero valid identities.
echo "==> Trusting it for code signing"
security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$WORK/cert.pem"

echo
security find-identity -v -p codesigning | grep "$NAME" || {
    echo "ERROR: identity still not valid for code signing."; exit 1; }
echo
echo "Done. Now run: bash build_app.sh"
