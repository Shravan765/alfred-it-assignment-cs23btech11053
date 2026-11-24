'''Password hashing and high-entropy bearer-token helpers.'''

import base64
import hashlib
import hmac
import secrets

# Crockford-style base32: digits and letters without I, L, O, U (32 symbols, 5 bits each).
CODE_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'


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


def newRegistrationCode(settings) -> str:
	'''Generate a grouped one-time registration code from the fixed alphabet.'''
	characters = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(settings.codes.length))
	group_size = settings.codes.group_size
	return '-'.join(characters[index:index + group_size] for index in range(0, len(characters), group_size))


def normaliseCode(text: str, settings) -> str | None:
	'''Return the canonical code (upper case, no dashes or spaces) or None if malformed.'''
	canonical = text.upper().replace('-', '').replace(' ', '')
	if len(canonical) != settings.codes.length or any(char not in CODE_ALPHABET for char in canonical):
		return None
	return canonical
