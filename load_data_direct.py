"""
Load Initial Data - Direct Script
ضعه في الجذر وشغله مباشرة
"""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
import random

from apps.core.models import Facility, Modality, Exam
from apps.protocols.models import ProtocolTemplate
from apps.core.constants import UserRole

User = get_user_model()


def create_facilities():
    """إنشاء المستشفيات"""
    facilities_data = [
        {'code': 'MAIN', 'name': 'Main Hospital', 'hl7_facility_id': 'MAIN_001'},
        {'code': 'BRANCH1', 'name': 'Branch Hospital 1', 'hl7_facility_id': 'BR1_001'},
        {'code': 'CLINIC', 'name': 'Outpatient Clinic', 'hl7_facility_id': 'CLINIC_001'},
    ]
    
    facilities = []
    for data in facilities_data:
        facility, created = Facility.objects.get_or_create(
            code=data['code'],
            defaults=data
        )
        facilities.append(facility)
        print(f'✓ {"Created" if created else "Found"} facility: {facility.name}')
    
    return facilities


def create_modalities():
    """إنشاء الأجهزة"""
    modalities_data = [
        {'code': 'CT', 'name': 'Computed Tomography', 'requires_qc': True},
        {'code': 'MR', 'name': 'Magnetic Resonance Imaging', 'requires_qc': True},
        {'code': 'XR', 'name': 'X-Ray', 'requires_qc': True},
        {'code': 'US', 'name': 'Ultrasound', 'requires_qc': False},
        {'code': 'NM', 'name': 'Nuclear Medicine', 'requires_qc': True},
    ]
    
    modalities = []
    for data in modalities_data:
        modality, created = Modality.objects.get_or_create(
            code=data['code'],
            defaults=data
        )
        modalities.append(modality)
        print(f'✓ {"Created" if created else "Found"} modality: {modality.name}')
    
    return modalities


def create_users(facilities):
    """إنشاء المستخدمين"""
    users_data = [
        {
            'email': 'radiologist@test.com',
            'username': 'radiologist',
            'first_name': 'Ahmed',
            'last_name': 'Al-Radiologist',
            'role': UserRole.RADIOLOGIST,
            'password': 'password123'
        },
        {
            'email': 'tech@test.com',
            'username': 'technologist',
            'first_name': 'Fatima',
            'last_name': 'Al-Tech',
            'role': UserRole.TECHNOLOGIST,
            'password': 'password123'
        },
        {
            'email': 'supervisor@test.com',
            'username': 'supervisor',
            'first_name': 'Mohammed',
            'last_name': 'Al-Supervisor',
            'role': UserRole.SUPERVISOR,
            'password': 'password123'
        },
    ]
    
    users = []
    for data in users_data:
        password = data.pop('password')
        user, created = User.objects.get_or_create(
            email=data['email'],
            defaults=data
        )
        if created:
            user.set_password(password)
            user.email_verified = True
            user.save()
            
            # Add facility access
            user.facilities.add(*facilities)
            user.primary_facility = facilities[0]
            user.save()
            
            print(f'✓ Created user: {user.email}')
        else:
            print(f'✓ Found user: {user.email}')
        
        users.append(user)
    
    return users


def create_protocols(modalities, facilities):
    """إنشاء البروتوكولات"""
    protocols_data = [
        {
            'code': 'CT_HEAD_NC',
            'name': 'CT Head Non-Contrast',
            'modality': 'CT',
            'body_part': 'Head',
            'requires_contrast': False,
            'priority': 10,
            'clinical_keywords': ['stroke', 'head injury', 'trauma'],
        },
        {
            'code': 'CT_CHEST_PE',
            'name': 'CT Chest PE Protocol',
            'modality': 'CT',
            'body_part': 'Chest',
            'requires_contrast': True,
            'contrast_phase': 'ARTERIAL',
            'priority': 15,
            'clinical_keywords': ['pulmonary embolism', 'PE', 'chest pain'],
        },
        {
            'code': 'MR_BRAIN_WO',
            'name': 'MRI Brain Without Contrast',
            'modality': 'MR',
            'body_part': 'Head',
            'requires_contrast': False,
            'priority': 10,
            'clinical_keywords': ['headache', 'seizure', 'stroke'],
        },
        {
            'code': 'XR_CHEST_2V',
            'name': 'Chest X-Ray 2 Views',
            'modality': 'XR',
            'body_part': 'Chest',
            'requires_contrast': False,
            'priority': 5,
            'clinical_keywords': ['cough', 'fever', 'pneumonia'],
        },
    ]
    
    protocols = []
    for data in protocols_data:
        modality_code = data.pop('modality')
        modality = next(m for m in modalities if m.code == modality_code)
        
        protocol, created = ProtocolTemplate.objects.get_or_create(
            code=data['code'],
            defaults={
                **data,
                'modality': modality,
                'is_active': True,
                'is_default': data.get('priority', 100) <= 10,
            }
        )
        protocols.append(protocol)
        print(f'✓ {"Created" if created else "Found"} protocol: {protocol.code}')
    
    return protocols


def create_exams(facilities, modalities):
    """إنشاء فحوصات تجريبية"""
    patient_names = ['Ahmed Mohammed', 'Fatima Ali', 'Sara Abdullah']
    
    exams = []
    for i in range(10):
        modality = random.choice(modalities)
        facility = random.choice(facilities)
        exam_date = timezone.now() - timedelta(days=random.randint(0, 30))
        
        exam, created = Exam.objects.get_or_create(
            accession_number=f'ACC2024{str(i+1).zfill(6)}',
            defaults={
                'order_id': f'ORD{str(i+1).zfill(6)}',
                'mrn': f'MRN{str(random.randint(100000, 999999))}',
                'patient_name': random.choice(patient_names),
                'patient_gender': random.choice(['M', 'F']),
                'facility': facility,
                'modality': modality,
                'procedure_name': f'{modality.code} Procedure',
                'exam_datetime': exam_date,
                'status': 'COMPLETED',
            }
        )
        exams.append(exam)
        if created:
            print(f'✓ Created exam: {exam.accession_number}')
    
    return exams


def main():
    """التنفيذ الرئيسي"""
    print('🚀 Loading initial data...\n')
    
    print('1. Creating facilities...')
    facilities = create_facilities()
    
    print('\n2. Creating modalities...')
    modalities = create_modalities()
    
    print('\n3. Creating users...')
    users = create_users(facilities)
    
    print('\n4. Creating protocols...')
    protocols = create_protocols(modalities, facilities)
    
    print('\n5. Creating exams...')
    exams = create_exams(facilities, modalities)
    
    print('\n✅ Initial data loaded successfully!')
    print(f'   - {len(facilities)} facilities')
    print(f'   - {len(modalities)} modalities')
    print(f'   - {len(users)} users')
    print(f'   - {len(protocols)} protocols')
    print(f'   - {len(exams)} exams')
    
    print('\n🔐 Test Users:')
    print('   radiologist@test.com / password123')
    print('   tech@test.com / password123')
    print('   supervisor@test.com / password123')


if __name__ == '__main__':
    main()