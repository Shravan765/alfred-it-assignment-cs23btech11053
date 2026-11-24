'''Password hashing and high-entropy bearer-token helpers.'''

import base64
import hashlib
import hmac
import secrets


def hashPassword(password: str, settings) -> str:
	'''Hash a password with a fresh salt and configured scrypt parameters.'''
	salt = secrets.token_bytes(16)
	maxmem = 256 * settings.auth.scrypt_n * settings.auth.scrypt_r
	digest = hashlib.scrypt(password.encode(), salt=salt, n=settings.auth.scrypt_n, r=settings.auth.scrypt_r, p=settings.auth.scrypt_p, maxmem=maxmem)
	return '$'.join(('scrypt', str(settings.auth.scrypt_n), str(settings.auth.scrypt_r), str(settings.auth.scrypt_p), base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))


def verifyPassword(password: str, encoded: str) -> bool:
	'''Verify a password against the self-describing scrypt hash.'''
	try:
		algorithm, cost_n, cost_r, cost_p, salt_text, digest_text = encoded.split('$')
		if algorithm != 'scrypt':
			return False
		salt = base64.b64decode(salt_text)
		expected = base64.b64decode(digest_text)
		maxmem = 256 * int(cost_n) * int(cost_r)
		actual = hashlib.scrypt(password.encode(), salt=salt, n=int(cost_n), r=int(cost_r), p=int(cost_p), dklen=len(expected), maxmem=maxmem)
		return hmac.compare_digest(actual, expected)
	except (ValueError, TypeError):
		return False


def newToken(settings) -> str:
	'''Generate a URL-safe random bearer token.'''
	return secrets.token_urlsafe(settings.auth.token_bytes)


def hashToken(token: str) -> str:
	'''Return the SHA-256 digest used for token storage.'''
	return hashlib.sha256(token.encode()).hexdigest()
