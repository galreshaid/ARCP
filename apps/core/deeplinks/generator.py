"""
Deep Link Generator
Creates secure, time-limited links for exam access
"""
import jwt
from datetime import datetime, timedelta
from django.conf import settings
from django.urls import reverse
from typing import Dict, Optional


class DeepLinkGenerator:
    """
    Generates secure deep links with JWT tokens
    """
    
    def __init__(self):
        self.secret_key = settings.DEEPLINK_SECRET_KEY
        self.algorithm = settings.DEEPLINK_ALGORITHM
        self.expiry_hours = settings.DEEPLINK_EXPIRY_HOURS

    def generate_qc_link(
        self,
        exam_id: str,
        accession_number: str,
        mrn: str,
        facility_code: str,
        user_id: Optional[str] = None,
        expiry_hours: Optional[int] = None
    ) -> str:
        """
        Generate QC deep link
        
        Args:
            exam_id: Exam UUID
            accession_number: Accession number
            mrn: Patient MRN
            facility_code: Facility code
            user_id: Optional specific user ID
            expiry_hours: Override default expiry
            
        Returns:
            Full URL with signed token
        """
        payload = self._create_payload(
            link_type='qc',
            exam_id=exam_id,
            accession_number=accession_number,
            mrn=mrn,
            facility_code=facility_code,
            user_id=user_id,
            expiry_hours=expiry_hours
        )
        
        token = self._encode_token(payload)
        path = reverse('qc:deeplink-entry')
        
        return f"{self._get_base_url()}{path}?token={token}"

    def generate_contrast_link(
        self,
        exam_id: str,
        accession_number: str,
        mrn: str,
        facility_code: str,
        user_id: Optional[str] = None,
        expiry_hours: Optional[int] = None
    ) -> str:
        """
        Generate Contrast documentation deep link
        """
        payload = self._create_payload(
            link_type='contrast',
            exam_id=exam_id,
            accession_number=accession_number,
            mrn=mrn,
            facility_code=facility_code,
            user_id=user_id,
            expiry_hours=expiry_hours
        )
        
        token = self._encode_token(payload)
        path = reverse('contrast:deeplink-entry')
        
        return f"{self._get_base_url()}{path}?token={token}"

    def generate_protocol_link(
        self,
        exam_id: str,
        accession_number: str,
        mrn: str,
        facility_code: str,
        user_id: Optional[str] = None,
        expiry_hours: Optional[int] = None
    ) -> str:
        """
        Generate Protocol assignment deep link
        """
        payload = self._create_payload(
            link_type='protocol',
            exam_id=exam_id,
            accession_number=accession_number,
            mrn=mrn,
            facility_code=facility_code,
            user_id=user_id,
            expiry_hours=expiry_hours
        )
        
        token = self._encode_token(payload)
        path = reverse('protocols:deeplink-entry')
        
        return f"{self._get_base_url()}{path}?token={token}"

    def _create_payload(
        self,
        link_type: str,
        exam_id: str,
        accession_number: str,
        mrn: str,
        facility_code: str,
        user_id: Optional[str] = None,
        expiry_hours: Optional[int] = None
    ) -> Dict:
        """
        Create JWT payload
        """
        now = datetime.utcnow()
        expiry = expiry_hours or self.expiry_hours
        
        payload = {
            'type': link_type,
            'exam_id': str(exam_id),
            'accession_number': accession_number,
            'mrn': mrn,
            'facility_code': facility_code,
            'iat': now,  # Issued at
            'exp': now + timedelta(hours=expiry),  # Expiration
            'nbf': now,  # Not before
        }
        
        if user_id:
            payload['user_id'] = str(user_id)
        
        return payload

    def _encode_token(self, payload: Dict) -> str:
        """
        Encode payload to JWT token
        """
        return jwt.encode(
            payload,
            self.secret_key,
            algorithm=self.algorithm
        )

    def _get_base_url(self) -> str:
        """
        Get base URL from settings
        """
        # In production, this should come from settings
        # For now, return placeholder
        return getattr(settings, 'SITE_URL', 'http://localhost:8000')


# Singleton instance
deeplink_generator = DeepLinkGenerator()