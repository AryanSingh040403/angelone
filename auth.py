import os
import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

def get_auth_session():
    """
    Loads credentials from the environment (loaded from .env), generates a TOTP, 
    logs into AngelOne SmartAPI, and returns the authentication tokens.
    
    Returns:
        tuple: (smart_connect, auth_token, feed_token)
        
    Raises:
        ValueError: If required environment variables are missing or invalid.
        RuntimeError: If TOTP generation or login session creation fails.
    """
    # Load environment variables from .env file
    # Check parent directory first, then local directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    parent_env = os.path.join(parent_dir, ".env")
    local_env = os.path.join(script_dir, ".env")
    
    if os.path.exists(parent_env):
        env_path = parent_env
    else:
        env_path = local_env
        
    load_dotenv(dotenv_path=env_path)
    
    # Retrieve credentials
    api_key = os.getenv("API_KEY", "").strip(" '\"\r\n\t")
    client_id = os.getenv("CLIENT_ID", "").strip(" '\"\r\n\t")
    mpin = os.getenv("MPIN", "").strip(" '\"\r\n\t")
    totp_secret = os.getenv("TOTP_SECRET", "").strip(" '\"\r\n\t")
    
    # Validate credentials exist
    missing_vars = []
    if not api_key:
        missing_vars.append("API_KEY")
    if not client_id:
        missing_vars.append("CLIENT_ID")
    if not mpin:
        missing_vars.append("MPIN")
    if not totp_secret:
        missing_vars.append("TOTP_SECRET")
        
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}. "
            f"Please ensure they are defined in your .env file at {env_path}"
        )
        
    # Generate TOTP using pyotp
    import logging
    try:
        # Try raw secret first (just stripped)
        totp = pyotp.TOTP(totp_secret).now()
        logging.getLogger("Auth").info(f"Generated TOTP for authentication: {totp}")
    except Exception as raw_err:
        try:
            # Fallback to cleaned and padded version if raw fails
            clean_secret = totp_secret.replace(" ", "").upper()
            missing_padding = len(clean_secret) % 8
            if missing_padding:
                clean_secret += '=' * (8 - missing_padding)
            totp = pyotp.TOTP(clean_secret).now()
            logging.getLogger("Auth").info(f"Generated TOTP for authentication (cleaned fallback): {totp}")
        except Exception as clean_err:
            raise RuntimeError(
                f"Failed to generate TOTP from TOTP_SECRET.\n"
                f"  Raw attempt failed: {raw_err}\n"
                f"  Cleaned attempt failed: {clean_err}"
            )
        
    # Initialize SmartConnect and generate session
    try:
        smart_connect = SmartConnect(api_key=api_key)
        session_data = smart_connect.generateSession(client_id, mpin, totp)
    except Exception as e:
        raise RuntimeError(f"Network error or exception during SmartAPI session creation: {e}")
        
    # Handle API response status errors
    if not session_data.get("status"):
        message = session_data.get("message", "Unknown error response from API")
        error_code = session_data.get("errorcode", "No error code")
        raise RuntimeError(
            f"AngelOne Login failed: {message} (Error Code: {error_code}). "
            "Please verify your credentials and check if your TOTP secret is correct."
        )
        
    # Extract tokens from response
    data = session_data.get("data", {})
    auth_token = data.get("jwtToken")
    feed_token = data.get("feedToken")
    
    if not auth_token or not feed_token:
        raise RuntimeError(
            "Successful login response did not contain both 'jwtToken' and 'feedToken'. "
            f"Response structure: {list(data.keys())}"
        )
        
    return smart_connect, auth_token, feed_token

if __name__ == "__main__":
    # Test script run
    try:
        print("Attempting to connect to SmartAPI using .env credentials...")
        smart_connect, auth_token, feed_token = get_auth_session()
        print("SUCCESS: Logged in and generated tokens.")
        print(f"Auth Token (JWT): {auth_token[:15]}...{auth_token[-15:]}")
        print(f"Feed Token:       {feed_token[:15]}...{feed_token[-15:]}")
    except Exception as e:
        print(f"ERROR: {e}")
