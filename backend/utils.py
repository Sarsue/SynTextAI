from firebase_admin import auth
import base64
import re
import hashlib



def decode_firebase_token(token):
    try:
        # Verify the token
        decoded_token = auth.verify_id_token(token)
        # Access user information from decoded token
        display_name = decoded_token.get('name', None)
        email = decoded_token.get('email', None)
        user_id = decoded_token.get('user_id', None)
        return True, {'name': display_name, 'email': email, 'user_id': user_id}
    except auth.ExpiredIdTokenError:
        return False, {'error': 'Token has expired'}
    except auth.InvalidIdTokenError as e:
        print(f'Invalid Token Error: {e}')
        return False, {'error': 'Invalid token'}
    except Exception as e:
        return False, {'error': str(e)}


def get_user_id(token):
    token = token.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)
    return success, user_info
   
