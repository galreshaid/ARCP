"""
ORM Message Parser
Parse HL7 ORM^O01 messages from GE/RIS
"""
from datetime import datetime
from typing import Dict, Optional
import re


class ORMParser:
    """
    Parse HL7 ORM^O01 Order messages
    """
    
    def __init__(self, raw_message: str):
        self.raw_message = raw_message
        self.segments = self._split_segments(raw_message)
        self.segment_dict = self._build_segment_dict()
    
    def _split_segments(self, message: str) -> list:
        """Split message into segments"""
        normalized = (message or '').replace('\r\n', '\n').replace('\r', '\n')
        if '\n' in normalized:
            return [segment.strip() for segment in normalized.split('\n') if segment.strip()]

        cleaned = normalized.strip()
        if not cleaned:
            return []

        # Fallback for messages delivered as a single line.
        pattern = r'(?=(?:MSH|PID|PV1|ORC|OBR|OBX|NTE)\|)'
        segments = re.split(pattern, cleaned)
        return [segment.strip() for segment in segments if segment.strip()]
    
    def _build_segment_dict(self) -> Dict:
        """Build dictionary of segments by type"""
        result = {}
        for segment in self.segments:
            if '|' in segment:
                seg_type = segment.split('|')[0]
                if seg_type not in result:
                    result[seg_type] = []
                result[seg_type].append(segment)
        return result
    
    def parse(self) -> Dict:
        """
        Parse ORM message and extract relevant data
        """
        try:
            data = {
                'message_info': self._parse_msh(),
                'patient': self._parse_pid(),
                'visit': self._parse_pv1(),
                'order': self._parse_orc(),
                'observation_request': self._parse_obr(),
            }
            
            return data
        
        except Exception as e:
            raise ValueError(f"Failed to parse ORM message: {str(e)}")
    
    def _parse_msh(self) -> Dict:
        """Parse MSH (Message Header) segment"""
        if 'MSH' not in self.segment_dict:
            raise ValueError("MSH segment not found")
        
        msh = self.segment_dict['MSH'][0]
        fields = msh.split('|')
        
        # MSH is special - field separator is at position 1
        return {
            'message_type': self._get_field(fields, 8),  # MSH-9
            'message_control_id': self._get_field(fields, 9),  # MSH-10
            'message_datetime': self._parse_datetime(self._get_field(fields, 6)),  # MSH-7
            'sending_application': self._get_field(fields, 2),  # MSH-3
            'sending_facility': self._get_field(fields, 3),  # MSH-4
            'receiving_application': self._get_field(fields, 4),  # MSH-5
            'receiving_facility': self._get_field(fields, 5),  # MSH-6
        }
    
    def _parse_pid(self) -> Dict:
        """Parse PID (Patient Identification) segment"""
        if 'PID' not in self.segment_dict:
            return {}
        
        pid = self.segment_dict['PID'][0]
        fields = pid.split('|')
        
        # Parse patient name (PID-5)
        name_field = self._get_field(fields, 5)
        name_parts = name_field.split('^')
        
        return {
            'mrn': self._extract_mrn(self._get_field(fields, 3)),  # PID-3
            'national_id': self._extract_national_id(self._get_field(fields, 4)),  # PID-4
            'patient_name': {
                'family': self._get_component(name_parts, 0),
                'given': self._get_component(name_parts, 1),
                'middle': self._get_component(name_parts, 2),
            },
            'dob': self._parse_date(self._get_field(fields, 7)),  # PID-7
            'gender': self._get_field(fields, 8),  # PID-8
            'phone': self._extract_phone(self._get_field(fields, 13)),  # PID-13
        }
    
    def _parse_pv1(self) -> Dict:
        """Parse PV1 (Patient Visit) segment"""
        if 'PV1' not in self.segment_dict:
            return {}
        
        pv1 = self.segment_dict['PV1'][0]
        fields = pv1.split('|')
        
        return {
            'patient_class': self._get_field(fields, 2),  # PV1-2
            'location': self._get_field(fields, 3),  # PV1-3
            'attending_doctor': self._parse_doctor(self._get_field(fields, 7)),  # PV1-7
            'visit_number': self._get_field(fields, 19),  # PV1-19
        }
    
    def _parse_orc(self) -> Dict:
        """Parse ORC (Common Order) segment"""
        if 'ORC' not in self.segment_dict:
            return {}
        
        orc = self.segment_dict['ORC'][0]
        fields = orc.split('|')
        
        return {
            'order_control': self._get_field(fields, 1),  # ORC-1 (NW = New Order)
            'placer_order_number': self._get_field(fields, 2),  # ORC-2
            'filler_order_number': self._get_field(fields, 3),  # ORC-3
            'order_status': self._get_field(fields, 5),  # ORC-5
            'order_datetime': self._parse_datetime(self._get_field(fields, 9)),  # ORC-9
            'ordering_provider': self._parse_doctor(self._get_field(fields, 12)),  # ORC-12
            'order_reason': self._get_field(fields, 16),  # ORC-16
        }
    
    def _parse_obr(self) -> Dict:
        """Parse OBR (Observation Request) segment"""
        if 'OBR' not in self.segment_dict:
            return {}
        
        obr = self.segment_dict['OBR'][0]
        fields = obr.split('|')
        
        # Parse procedure code (OBR-4)
        procedure_field = self._get_field(fields, 4)
        procedure_parts = procedure_field.split('^')
        diagnosis_field = self._get_field(fields, 31)
        diagnosis_parts = diagnosis_field.split('^')

        return {
            'set_id': self._get_field(fields, 1),  # OBR-1
            'placer_order_number': self._get_field(fields, 2),  # OBR-2
            'filler_order_number': self._get_field(fields, 3),  # OBR-3
            'procedure_code': self._get_component(procedure_parts, 0),
            'procedure_name': self._get_component(procedure_parts, 1),
            'procedure_coding_system': self._get_component(procedure_parts, 2),
            'requested_datetime': self._parse_datetime(self._get_field(fields, 6)),  # OBR-6
            'clinical_history': self._get_field(fields, 13),  # OBR-13
            'priority': self._get_field(fields, 27),  # OBR-27
            'reason_for_study': diagnosis_field,  # OBR-31
            'diagnosis_code': self._get_component(diagnosis_parts, 0),
            'diagnosis_description': self._clean_diagnosis_description(
                self._get_component(diagnosis_parts, 1)
            ),
            'diagnosis_coding_system': self._get_component(diagnosis_parts, 2),
        }
    
    # Helper methods
    
    def _get_field(self, fields: list, index: int) -> str:
        """Safely get field by index"""
        try:
            return fields[index] if index < len(fields) else ''
        except:
            return ''
    
    def _get_component(self, components: list, index: int) -> str:
        """Safely get component by index"""
        try:
            return components[index] if index < len(components) else ''
        except:
            return ''
    
    def _extract_mrn(self, patient_id_field: str) -> str:
        """Extract MRN from PID-3"""
        # Format: MRN^^^Type or multiple IDs separated by ~
        ids = patient_id_field.split('~')
        for id_str in ids:
            if 'MRN' in id_str.upper():
                parts = id_str.split('^')
                return parts[0] if parts else ''
        # If no MRN found, return first ID
        if ids:
            return ids[0].split('^')[0]
        return ''
    
    def _extract_national_id(self, id_field: str) -> str:
        """Extract National ID from PID-4"""
        if id_field:
            parts = id_field.split('^')
            return parts[0] if parts else ''
        return ''
    
    def _extract_phone(self, phone_field: str) -> str:
        """Extract phone from PID-13"""
        if phone_field:
            parts = phone_field.split('^')
            return parts[0] if parts else ''
        return ''
    
    def _parse_doctor(self, doctor_field: str) -> Dict:
        """Parse doctor name field"""
        if not doctor_field:
            return {}
        
        parts = doctor_field.split('^')
        return {
            'id': self._get_component(parts, 0),
            'family_name': self._get_component(parts, 1),
            'given_name': self._get_component(parts, 2),
            'middle_name': self._get_component(parts, 3),
        }
    
    def _parse_datetime(self, dt_str: str) -> Optional[str]:
        """Parse HL7 datetime to ISO format"""
        if not dt_str:
            return None
        
        # HL7 format: YYYYMMDDHHmmss
        try:
            if len(dt_str) >= 8:
                year = dt_str[0:4]
                month = dt_str[4:6]
                day = dt_str[6:8]
                hour = dt_str[8:10] if len(dt_str) >= 10 else '00'
                minute = dt_str[10:12] if len(dt_str) >= 12 else '00'
                second = dt_str[12:14] if len(dt_str) >= 14 else '00'
                
                dt = datetime(int(year), int(month), int(day), 
                            int(hour), int(minute), int(second))
                return dt.isoformat()
        except:
            pass
        
        return None
    
    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse HL7 date to ISO format"""
        if not date_str:
            return None
        
        # HL7 format: YYYYMMDD
        try:
            if len(date_str) >= 8:
                year = date_str[0:4]
                month = date_str[4:6]
                day = date_str[6:8]
                
                dt = datetime(int(year), int(month), int(day))
                return dt.date().isoformat()
        except:
            pass
        
        return None

    def _clean_diagnosis_description(self, value: str) -> str:
        if not value:
            return ''

        return value.replace(' -', '').strip()
