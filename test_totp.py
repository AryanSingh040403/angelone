import os
import sys
import pyotp
import binascii
from dotenv import load_dotenv

def diagnose_totp():
    print("="*60)
    print("ANGELONE TOTP DIAGNOSTIC TOOL")
    print("="*60)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    env_path = os.path.join(parent_dir, ".env")
    
    print(f"Checking for .env file at: {env_path}")
    if not os.path.exists(env_path):
        print("[ERROR] .env file not found!")
        print("Please ensure you have created the .env file in the same directory as this script.")
        return
    
    print("[SUCCESS] .env file found.")
    
    # Load .env explicitly
    load_dotenv(dotenv_path=env_path)
    
    totp_secret = os.getenv("TOTP_SECRET")
    if not totp_secret:
        print("[ERROR] TOTP_SECRET variable is not defined or is empty in .env!")
        return
        
    print(f"Loaded TOTP_SECRET from .env (length: {len(totp_secret)} characters).")
    
    # Clean secret key
    clean_secret = totp_secret.strip(" '\"\r\n\t").replace(" ", "").upper()
    print(f"Cleaned key (stripped quotes/spaces, uppercase, length: {len(clean_secret)}).")
    
    # Inspect characters
    non_b32_chars = []
    valid_b32_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567="
    for idx, char in enumerate(clean_secret):
        if char not in valid_b32_chars:
            non_b32_chars.append((idx + 1, char))
            
    if non_b32_chars:
        print(f"[WARNING] Key contains non-Base32 characters at the following 1-indexed positions:")
        for pos, char in non_b32_chars:
            print(f"  - Position {pos}: character '{char}' (Hex: {hex(ord(char))})")
        print("  Base32 character set only allows A-Z and 2-7. Please verify your secret key copy.")
    else:
        print("[SUCCESS] All characters in the key are valid Base32 characters.")
        
    # Check padding
    missing_padding = len(clean_secret) % 8
    padded_secret = clean_secret
    if missing_padding:
        padding_chars = '=' * (8 - missing_padding)
        padded_secret += padding_chars
        print(f"Key was not a multiple of 8. Automatically added padding: '{padding_chars}'")
        print(f"Padded key length: {len(padded_secret)}")
        
    # Attempt generation
    try:
        totp = pyotp.TOTP(padded_secret)
        current_code = totp.now()
        print("\n" + "="*60)
        print("[SUCCESS] TOTP GENERATED SUCCESSFULLY!")
        print(f"Generated 6-digit code: {current_code}")
        print("This means the script was able to parse your key without any errors.")
        print("="*60 + "\n")
    except Exception as e:
        print("\n" + "="*60)
        print("[ERROR] Failed to generate TOTP from key!")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Detail: {e}")
        
        # Check specific errors
        if isinstance(e, binascii.Error) or "padding" in str(e).lower():
            print("\n[SUGGESTION] This is a padding issue. Check if the secret key length is correct.")
        elif "digit" in str(e).lower() or "base32" in str(e).lower():
            print("\n[SUGGESTION] Non-Base32 character detected. Did you accidentally copy a space, a special character, or a letter outside A-Z or a number outside 2-7?")
            
        print("="*60 + "\n")

if __name__ == "__main__":
    diagnose_totp()
