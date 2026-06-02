"""Two-Factor Authentication (2FA/TOTP) service using PyOTP."""
import pyotp


def generate_totp_secret() -> str:
    """
    Generate a random base32 secret key for TOTP.

    Returns:
        Base32-encoded secret key for provisioning QR codes
    """
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str, issuer_name: str = "NextGenPlanFact") -> str:
    """
    Generate provisioning URI for QR code generation.

    Args:
        secret: Base32-encoded TOTP secret
        username: User identifier (usually email)
        issuer_name: Issuer name for authenticator apps (default: NextGenPlanFact)

    Returns:
        Provisioning URI string for QR code
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer_name)


def verify_totp_code(secret: str, code: str) -> bool:
    """
    Verify a 6-digit TOTP code provided by user.

    Args:
        secret: Base32-encoded TOTP secret
        code: User-provided 6-digit code

    Returns:
        True if code is valid, False otherwise
    """
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(code)
    except Exception:
        return False
