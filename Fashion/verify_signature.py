"""
Run this locally (django shell or plain python, adjust the import) to
sanity-check that your Python signing code and your key file agree with
what openssl produces for the same input. If these two don't match,
the bug is in key loading / code, not the Jenga portal.

    python verify_signature.py
"""

from base64 import b64encode
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

PRIVATE_KEY_PATH = "path/to/privatekey.pem"  # <-- point this at your real key
PLAIN_TEXT = "OR28922980077KES254722000000500.00"  # <-- any fixed test string

with open(PRIVATE_KEY_PATH, "r") as f:
    private_key = RSA.import_key(f.read())

digest = SHA256.new(PLAIN_TEXT.encode("utf-8"))
signature = pkcs1_15.new(private_key).sign(digest)
signature_b64 = b64encode(signature).decode("utf-8")

print("Plain text signed:", PLAIN_TEXT)
print("Signature (base64):", signature_b64)

print(
    """
Cross-check with openssl (run in a terminal, same directory as your key):

  echo -n "%s" > plaintext.txt
  openssl dgst -sha256 -sign privatekey.pem -out sig.bin plaintext.txt
  openssl base64 -in sig.bin -out sig.b64
  cat sig.b64

The output of `cat sig.b64` should be IDENTICAL (ignoring line wrapping)
to the "Signature (base64)" printed above. If they differ, something is
off in how the key is being read (e.g. wrong file, extra whitespace,
wrong key format) rather than in Jenga's verification.
"""
    % PLAIN_TEXT
)