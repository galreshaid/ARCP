"""
Core Constants and Enumerations
"""

# HL7 Message Types
class HL7MessageType:
    ORM = 'ORM^O01'  # Order Message
    ORR = 'ORR^O02'  # Order Response
    ORU = 'ORU^R01'  # Observation Result
    ACK = 'ACK'      # General Acknowledgment


# HL7 Segment IDs
class HL7Segment:
    MSH = 'MSH'  # Message Header
    PID = 'PID'  # Patient Identification
    PV1 = 'PV1'  # Patient Visit
    ORC = 'ORC'  # Common Order
    OBR = 'OBR'  # Observation Request
    OBX = 'OBX'  # Observation Result
    NTE = 'NTE'  # Notes and Comments


# HL7 Acknowledgment Codes
class HL7AckCode:
    AA = 'AA'  # Application Accept
    AE = 'AE'  # Application Error
    AR = 'AR'  # Application Reject


# Modality Codes
class ModalityCode:
    CT = 'CT'
    MR = 'MR'
    XR = 'XR'
    US = 'US'
    NM = 'NM'
    PT = 'PT'
    MG = 'MG'
    DX = 'DX'
    RF = 'RF'


PROTOCOL_REQUIRED_MODALITY_CODES = (
    ModalityCode.CT,
    ModalityCode.MR,
    ModalityCode.NM,
    ModalityCode.US,
    ModalityCode.RF,
)


# User Roles
class UserRole:
    RADIOLOGIST = 'RADIOLOGIST'
    TECHNOLOGIST = 'TECHNOLOGIST'
    SUPERVISOR = 'SUPERVISOR'
    FINANCE = 'FINANCE'
    ADMIN = 'ADMIN'
    VIEWER = 'VIEWER'


# Permissions
class Permission:
    # QC Permissions
    QC_VIEW = 'qc.view'
    QC_CREATE = 'qc.create'
    QC_EDIT = 'qc.edit'
    QC_APPROVE = 'qc.approve'
    QC_EVIDENCE_CAPTURE = 'qc.evidence_capture'
    QC_EVIDENCE_VIEW = 'qc.evidence_view'
    
    # Protocol Permissions
    PROTOCOL_VIEW = 'protocol.view'
    PROTOCOL_ASSIGN = 'protocol.assign'
    PROTOCOL_EDIT = 'protocol.edit'
    
    # Contrast Permissions
    CONTRAST_VIEW = 'contrast.view'
    CONTRAST_CREATE = 'contrast.create'
    CONTRAST_EDIT = 'contrast.edit'
    CONTRAST_APPROVE = 'contrast.approve'
    
    # Reporting
    REPORT_VIEW = 'report.view'
    REPORT_EXPORT = 'report.export'
    
    # Admin
    ADMIN_ACCESS = 'admin.access'
    AUDIT_VIEW = 'audit.view'
    MATERIAL_CATALOG_ADD = 'material_catalog.add'
    MATERIAL_CATALOG_EDIT = 'material_catalog.edit'


# Role-Permission Mapping
ROLE_PERMISSIONS = {
    UserRole.RADIOLOGIST: [
        Permission.QC_VIEW,
        Permission.QC_CREATE,
        Permission.QC_EDIT,
        Permission.QC_APPROVE,
        Permission.QC_EVIDENCE_CAPTURE,
        Permission.QC_EVIDENCE_VIEW,
        Permission.PROTOCOL_VIEW,
        Permission.PROTOCOL_ASSIGN,
        Permission.CONTRAST_VIEW,
        Permission.REPORT_VIEW,
    ],
    UserRole.TECHNOLOGIST: [
        Permission.CONTRAST_VIEW,
        Permission.CONTRAST_CREATE,
        Permission.CONTRAST_EDIT,
        Permission.QC_VIEW,
        Permission.PROTOCOL_VIEW,
    ],
    UserRole.SUPERVISOR: [
        Permission.QC_VIEW,
        Permission.QC_EDIT,
        Permission.QC_APPROVE,
        Permission.QC_EVIDENCE_CAPTURE,
        Permission.QC_EVIDENCE_VIEW,
        Permission.PROTOCOL_VIEW,
        Permission.CONTRAST_VIEW,
        Permission.CONTRAST_APPROVE,
        Permission.REPORT_VIEW,
        Permission.REPORT_EXPORT,
        Permission.AUDIT_VIEW,
    ],
    UserRole.VIEWER: [
        Permission.QC_VIEW,
        Permission.PROTOCOL_VIEW,
        Permission.CONTRAST_VIEW,
        Permission.REPORT_VIEW,
    ],
    UserRole.FINANCE: [
        Permission.CONTRAST_VIEW,
        Permission.REPORT_VIEW,
        Permission.REPORT_EXPORT,
    ],
}

# Admin gets all permissions (defined AFTER the dict is complete)
ROLE_PERMISSIONS[UserRole.ADMIN] = [
    Permission.ADMIN_ACCESS,
    Permission.AUDIT_VIEW,
    Permission.MATERIAL_CATALOG_ADD,
    Permission.MATERIAL_CATALOG_EDIT,
    Permission.QC_VIEW,
    Permission.QC_CREATE,
    Permission.QC_EDIT,
    Permission.QC_APPROVE,
    Permission.QC_EVIDENCE_CAPTURE,
    Permission.QC_EVIDENCE_VIEW,
    Permission.PROTOCOL_VIEW,
    Permission.PROTOCOL_ASSIGN,
    Permission.PROTOCOL_EDIT,
    Permission.CONTRAST_VIEW,
    Permission.CONTRAST_CREATE,
    Permission.CONTRAST_EDIT,
    Permission.CONTRAST_APPROVE,
    Permission.REPORT_VIEW,
    Permission.REPORT_EXPORT,
]


# QC Constants
class QCOutcome:
    PASS = 'PASS'
    CONDITIONAL = 'CONDITIONAL'
    FAIL = 'FAIL'


class QCCategory:
    POSITIONING = 'POSITIONING'
    MOTION_ARTIFACT = 'MOTION_ARTIFACT'
    EXPOSURE = 'EXPOSURE'
    COMPLETENESS = 'COMPLETENESS'
    SIDE_CORRECTNESS = 'SIDE_CORRECTNESS'
    PROTOCOL_COMPLIANCE = 'PROTOCOL_COMPLIANCE'
    OTHER = 'OTHER'


# Evidence Types
class EvidenceType:
    SCREENSHOT = 'SCREENSHOT'
    ANNOTATION = 'ANNOTATION'
    DOCUMENT = 'DOCUMENT'


# Contrast Administration
class ContrastRoute:
    IV = 'IV'
    ORAL = 'ORAL'
    RECTAL = 'RECTAL'
    INTRATHECAL = 'INTRATHECAL'
    INTRA_ARTICULAR = 'INTRA_ARTICULAR'


class InjectionMethod:
    MANUAL = 'MANUAL'
    POWER_INJECTOR = 'POWER_INJECTOR'


class ReactionSeverity:
    MILD = 'MILD'
    MODERATE = 'MODERATE'
    SEVERE = 'SEVERE'
    LIFE_THREATENING = 'LIFE_THREATENING'


# Measurement Units
class MeasurementUnit:
    ML = 'mL'
    CC = 'cc'
    MG = 'mg'
    G = 'g'
    MG_ML = 'mg/mL'
    ML_SEC = 'mL/sec'
    IU = 'IU'


# Notification Types
class NotificationType:
    QC_FAIL = 'QC_FAIL'
    QC_CONDITIONAL = 'QC_CONDITIONAL'
    CONTRAST_REACTION = 'CONTRAST_REACTION'
    PROTOCOL_ASSIGNED = 'PROTOCOL_ASSIGNED'
    SYSTEM_ALERT = 'SYSTEM_ALERT'


# Audit Action Types
class AuditAction:
    CREATE = 'CREATE'
    READ = 'READ'
    UPDATE = 'UPDATE'
    DELETE = 'DELETE'
    LOGIN = 'LOGIN'
    LOGOUT = 'LOGOUT'
    EVIDENCE_VIEW = 'EVIDENCE_VIEW'
    EVIDENCE_CAPTURE = 'EVIDENCE_CAPTURE'
    HL7_SEND = 'HL7_SEND'
    HL7_RECEIVE = 'HL7_RECEIVE'
