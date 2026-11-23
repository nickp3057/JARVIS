#!/usr/bin/env bash
# Device certificates for away access (REMOTE_AND_MONEY_PLAN.md, Part 1, item 1b).
#
# Lightsail's Caddy only lets a device through to JARVIS at
# https://jarvis.nickolaspearson.com when it shows a client certificate signed
# by Nick's private device CA. Everything lives OUTSIDE the repo, in
# ~/.jarvis-devices (override with JARVIS_DEVICES_DIR); never commit any of it.
#
#   ops/remote/make-device-cert.sh ca              # once: the CA (key stays on this Mac)
#   ops/remote/make-device-cert.sh device iphone   # iphone.p12 (asks for a passphrase, or JARVIS_P12_PASS)
#   ops/remote/make-device-cert.sh device ipad-air
#   ops/remote/make-device-cert.sh device ipad-mini
#   ops/remote/make-device-cert.sh device mac
#   ops/remote/make-device-cert.sh device core     # core.crt + core.key for the Surface's away monitor
#   ops/remote/make-device-cert.sh install-ca      # copy the CA cert into nickolaspearson-site/deploy
#
# AirDrop each .p12 to its device, then in the companion: Settings > Server >
# Away access > Import device certificate. Lost a device? Run `ca --rotate`,
# re-issue the others and redeploy the site (the old CA stops working).
set -euo pipefail
DIR=${JARVIS_DEVICES_DIR:-"$HOME/.jarvis-devices"}
SITE=${NICK_SITE_DIR:-"$(cd "$(dirname "$0")/../../.." && pwd)/nickolaspearson-site"}
DAYS=730
umask 077
mkdir -p "$DIR"

case "${1:-}" in
ca)
    if [[ -f "$DIR/ca.key" && "${2:-}" != "--rotate" ]]; then
        echo "CA already exists in $DIR (use 'ca --rotate' to replace it)."; exit 0
    fi
    [[ -f "$DIR/ca.key" ]] && mv "$DIR/ca.key" "$DIR/ca.key.old-$(date +%Y%m%d%H%M%S)"
    openssl ecparam -name prime256v1 -genkey -noout -out "$DIR/ca.key"
    openssl req -x509 -new -key "$DIR/ca.key" -sha256 -days 3650 -subj "/CN=JARVIS Devices CA" \
        -addext "basicConstraints=critical,CA:true" -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out "$DIR/ca.crt"
    chmod 600 "$DIR/ca.key"; chmod 644 "$DIR/ca.crt"
    echo "CA made: $DIR/ca.crt (key: $DIR/ca.key, keep it on this Mac only)."
    echo "Next: $0 install-ca, then issue each device."
    ;;
device)
    NAME=${2:?"device name, e.g. iphone"}
    [[ "$NAME" =~ ^[a-z0-9-]+$ ]] || { echo "Name: lowercase letters, digits and dashes."; exit 1; }
    [[ -f "$DIR/ca.key" ]] || { echo "No CA yet: run '$0 ca' first."; exit 1; }
    openssl ecparam -name prime256v1 -genkey -noout -out "$DIR/$NAME.key"
    openssl req -new -key "$DIR/$NAME.key" -subj "/CN=jarvis-$NAME" -out "$DIR/$NAME.csr"
    printf 'basicConstraints=CA:false\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > "$DIR/$NAME.ext"
    openssl x509 -req -in "$DIR/$NAME.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial \
        -days "$DAYS" -sha256 -extfile "$DIR/$NAME.ext" -out "$DIR/$NAME.crt"
    rm -f "$DIR/$NAME.csr" "$DIR/$NAME.ext"
    if [[ "$NAME" == core ]]; then
        echo "Core cert: $DIR/core.crt + $DIR/core.key"
        echo "Copy both to the Surface (e.g. ~/jarvis/memory/away/), chmod 600 the key, and set in .env:"
        echo "  JARVIS_AWAY_CERT=~/jarvis/memory/away/core.crt"
        echo "  JARVIS_AWAY_KEY=~/jarvis/memory/away/core.key"
        exit 0
    fi
    # iOS's SecPKCS12Import wants the older PBE/MAC algorithms; OpenSSL 3's defaults fail there.
    LEGACY=()
    openssl version | grep -Eq '^OpenSSL [3-9]' && LEGACY=(-legacy)
    openssl pkcs12 -export "${LEGACY[@]}" -inkey "$DIR/$NAME.key" -in "$DIR/$NAME.crt" -certfile "$DIR/ca.crt" \
        -name "JARVIS $NAME" -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 -out "$DIR/$NAME.p12" \
        ${JARVIS_P12_PASS:+-passout env:JARVIS_P12_PASS}
    rm -f "$DIR/$NAME.key"   # the .p12 holds it now
    echo "Made $DIR/$NAME.p12. AirDrop it to the device and import it in the companion's Settings."
    ;;
install-ca)
    [[ -f "$DIR/ca.crt" ]] || { echo "No CA yet: run '$0 ca' first."; exit 1; }
    [[ -d "$SITE/deploy" ]] || { echo "nickolaspearson-site not found at $SITE (set NICK_SITE_DIR)."; exit 1; }
    install -m 644 "$DIR/ca.crt" "$SITE/deploy/jarvis-devices-ca.pem"
    echo "Copied. Deploy the site to turn on the JARVIS proxy: (cd $SITE && bash deploy/from-mac.sh)"
    ;;
*)
    sed -n '2,20p' "$0"; exit 1
    ;;
esac
