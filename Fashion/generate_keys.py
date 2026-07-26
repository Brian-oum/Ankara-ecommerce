from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from pathlib import Path

# Create the keys directory if it doesn't exist
keys_dir = Path("keys")
keys_dir.mkdir(exist_ok=True)

# Generate a 2048-bit RSA private key
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
)

# Save the private key
with open(keys_dir / "private.pem", "wb") as f:
    f.write(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

# Generate the public key
public_key = private_key.public_key()

# Save the public key
with open(keys_dir / "public.pem", "wb") as f:
    f.write(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

print("RSA key pair generated successfully!")
print(f"Private key: {keys_dir / 'private.pem'}")
print(f"Public key: {keys_dir / 'public.pem'}")