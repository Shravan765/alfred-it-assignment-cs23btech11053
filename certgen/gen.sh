#!/bin/sh
set -eu
mkdir -p /certs
if [ -s /certs/ca.pem ] && [ -s /certs/leaf.pem ] && [ -s /certs/leaf.key ]; then
	exit 0
fi
umask 077
openssl req -x509 -newkey rsa:3072 -nodes -keyout /certs/ca.key -out /certs/ca.pem -days 825 -subj '/CN=Lab PC Agent temporary CA' -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign'
openssl req -newkey rsa:3072 -nodes -keyout /certs/leaf.key -out /certs/leaf.csr -subj '/CN=proxy'
cat > /tmp/extensions.cnf <<'EXT'
subjectAltName=DNS:localhost,DNS:proxy,IP:127.0.0.1,IP:::1
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EXT
openssl x509 -req -in /certs/leaf.csr -CA /certs/ca.pem -CAkey /certs/ca.key -CAcreateserial -out /certs/leaf.pem -days 825 -sha256 -extfile /tmp/extensions.cnf
cat /certs/ca.pem >> /certs/leaf.pem
rm -f /certs/ca.key /certs/ca.srl /certs/leaf.csr /tmp/extensions.cnf
chmod 644 /certs/ca.pem /certs/leaf.pem
chmod 600 /certs/leaf.key
