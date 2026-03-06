"""
Deep Link Validator
Validates and decodes deep link tokens
"""
import jwt
from django.conf import settings
from django.core.exceptions import PermissionDenied
from typing import Dict, Optional
from datetime import datetime


class DeepLinkValidationError(Exception):
    """Custom exception for deep link validation errors"""
    pass


class DeepLinkValidator:
    """
    Validates deep link tokens and extracts payload
    """
    
    def __init__(self):
        self.secret_key = settings.DEEPLINK_SECRET_KEY
        self.algorithm = settings.DEEPLINK_ALGORITHM

    def validate_and_decode(self, token: str) -> Dict:
        """
        Validate token and return decoded payload
        
        Args:
            token: JWT token string
            
        Returns:
            Decoded payload dictionary
            
        Raises:
            DeepLinkValidationError: If token is invalid
        """
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={
                    'verify_exp': True,  # Verify expiration
                    'verify_nbf': True,  # Verify not-before
                }
            )
            
            # Additional validation
            self._validate_payload_structure(payload)
            
            return payload
            
        except jwt.ExpiredSignatureError:
            raise DeepLinkValidationError("Link has expired")
        
        except jwt.InvalidTokenError as e:
            raise DeepLinkValidationError(f"Invalid token: {str(e)}")

    def validate_for_user(
        self,
        token: str,
        user,
        required_type: Optional[str] = None
    ) -> Dict:
        """
        Validate token and check user permissions
        
        Args:
            token: JWT token
            user: Django User instance
            required_type: Required link type ('qc', 'contrast', 'protocol')
            
        Returns:
            Decoded payload
            
        Raises:
            DeepLinkValidationError: If validation fails
            PermissionDenied: If user lacks permissions
        """
        payload = self.validate_and_decode(token)
        
        # Check if token is user-specific
        if 'user_id' in payload:
            if str(user.id) != payload['user_id']:
                raise PermissionDenied("This link is not assigned to you")
        
        # Check link type if specified
        if required_type and payload.get('type') != required_type:
            raise DeepLinkValidationError(
                f"Invalid link type. Expected: {required_type}, Got: {payload.get('type')}"
            )
        
        # Check user has required permissions
        link_type = payload.get('type')
        if link_type == 'qc':
            from apps.core.constants import Permission
            if not user.has_permission(Permission.QC_VIEW):
                raise PermissionDenied("You don't have permission to access QC")
        
        elif link_type == 'contrast':
            from apps.core.constants import Permission
            if not user.has_permission(Permission.CONTRAST_VIEW):
                raise PermissionDenied("You don't have permission to access Contrast")
        
        elif link_type == 'protocol':
            from apps.core.constants import Permission
            if not user.has_permission(Permission.PROTOCOL_VIEW):
                raise PermissionDenied("You don't have permission to access Protocols")
        
        return payload

    def _validate_payload_structure(self, payload: Dict):
        """
        Validate payload has required fields
        """
        required_fields = ['type', 'exam_id', 'accession_number', 'mrn', 'facility_code']
        
        missing_fields = [field for field in required_fields if field not in payload]
        
        if missing_fields:
            raise DeepLinkValidationError(
                f"Token missing required fields: {', '.join(missing_fields)}"
            )
        
        # Validate link type
        valid_types = ['qc', 'contrast', 'protocol']
        if payload['type'] not in valid_types:
            raise DeepLinkValidationError(
                f"Invalid link type: {payload['type']}"
            )

    def extract_exam_context(self, payload: Dict) -> Dict:
        """
        Extract exam context from validated payload
        
        Returns:
            Dictionary with exam identifiers
        """
        return {
            'exam_id': payload['exam_id'],
            'accession_number': payload['accession_number'],
            'mrn': payload['mrn'],
            'facility_code': payload['facility_code'],
            'link_type': payload['type'],
        }


# Singleton instance
deeplink_validator = DeepLinkValidator()