"""
Load Initial Test Data
لتجربة النظام بسرعة
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
import random

from apps.core.models import Facility, Modality, Exam
from apps.protocols.models import ProtocolTemplate
from apps.core.constants import UserRole

User = get_user_model()


class Command(BaseCommand):
    help = 'Load initial test data for development'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Delete existing data first'
        )

    def handle(self, *args, **options):
        if options['flush']:
            self.stdout.write(self.style.WARNING('Flushing existing data...'))
            # Be careful with this in production!
            Exam.objects.all().delete()
            ProtocolTemplate.objects.all().delete()
            Modality.objects.all().delete()
            Facility.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()

        self.stdout.write(self.style.SUCCESS('Loading initial data...'))
        
        # 1. Create Facilities
        self.stdout.write('Creating facilities...')
        facilities = self.create_facilities()
        
        # 2. Create Modalities
        self.stdout.write('Creating modalities...')
        modalities = self.create_modalities()
        
        # 3. Create Users
        self.stdout.write('Creating users...')
        users = self.create_users(facilities)
        
        # 4. Create Protocol Templates
        self.stdout.write('Creating protocol templates...')
        protocols = self.create_protocols(modalities, facilities)
        
        # 5. Create Sample Exams
        self.stdout.write('Creating sample exams...')
        exams = self.create_exams(facilities, modalities)
        
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Initial data loaded successfully!\n'
            f'   - {len(facilities)} facilities\n'
            f'   - {len(modalities)} modalities\n'
            f'   - {len(users)} users\n'
            f'   - {len(protocols)} protocols\n'
            f'   - {len(exams)} exams\n'
        ))
        
        self.stdout.write(self.style.SUCCESS('\n🔐 Test Users:'))
        self.stdout.write('   Radiologist: radiologist@test.com / password123')
        self.stdout.write('   Technologist: tech@test.com / password123')
        self.stdout.write('   Supervisor: supervisor@test.com / password123')

    def create_facilities(self):
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
            if created:
                self.stdout.write(f'  ✓ Created facility: {facility.name}')
        
        return facilities

    def create_modalities(self):
        modalities_data = [
            {
                'code': 'CT',
                'name': 'Computed Tomography',
                'requires_qc': True,
                'requires_contrast': True,
                'qc_checklist_template': {
                    'positioning': True,
                    'motion': True,
                    'exposure': True,
                    'artifacts': True
                }
            },
            {
                'code': 'MR',
                'name': 'Magnetic Resonance Imaging',
                'requires_qc': True,
                'requires_contrast': True,
                'qc_checklist_template': {
                    'positioning': True,
                    'motion': True,
                    'signal_quality': True,
                    'artifacts': True
                }
            },
            {
                'code': 'XR',
                'name': 'X-Ray',
                'requires_qc': True,
                'requires_contrast': False,
                'qc_checklist_template': {
                    'positioning': True,
                    'exposure': True,
                    'collimation': True
                }
            },
            {
                'code': 'US',
                'name': 'Ultrasound',
                'requires_qc': False,
                'requires_contrast': False,
            },
            {
                'code': 'NM',
                'name': 'Nuclear Medicine',
                'requires_qc': True,
                'requires_contrast': True,
            },
        ]
        
        modalities = []
        for data in modalities_data:
            modality, created = Modality.objects.get_or_create(
                code=data['code'],
                defaults=data
            )
            modalities.append(modality)
            if created:
                self.stdout.write(f'  ✓ Created modality: {modality.name}')
        
        return modalities

    def create_users(self, facilities):
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
                
                self.stdout.write(f'  ✓ Created user: {user.email}')
            
            users.append(user)
        
        return users

    def create_protocols(self, modalities, facilities):
        protocols_data = [
            # CT Protocols
            {
                'code': 'CT_HEAD_NC',
                'name': 'CT Head Non-Contrast',
                'modality': 'CT',
                'body_part': 'Head',
                'requires_contrast': False,
                'priority': 10,
                'clinical_keywords': ['stroke', 'head injury', 'trauma', 'headache'],
                'description': 'Non-contrast CT of the head for acute stroke, trauma, or other indications',
                'instructions': '1. Patient supine, head first\n2. Scout AP and lateral\n3. Axial slices 5mm thickness\n4. Bone and soft tissue windows',
            },
            {
                'code': 'CT_CHEST_PE',
                'name': 'CT Chest PE Protocol',
                'modality': 'CT',
                'body_part': 'Chest',
                'requires_contrast': True,
                'contrast_phase': 'ARTERIAL',
                'priority': 15,
                'clinical_keywords': ['pulmonary embolism', 'PE', 'chest pain', 'shortness of breath'],
                'description': 'CT pulmonary angiography for PE evaluation',
                'instructions': '1. IV access 18G or larger\n2. Contrast bolus timing\n3. Arterial phase acquisition',
            },
            {
                'code': 'CT_ABD_PELV',
                'name': 'CT Abdomen/Pelvis with Contrast',
                'modality': 'CT',
                'body_part': 'Abdomen',
                'requires_contrast': True,
                'contrast_phase': 'VENOUS',
                'priority': 20,
                'clinical_keywords': ['abdominal pain', 'mass', 'cancer', 'appendicitis'],
                'description': 'Standard abdomen and pelvis with IV contrast',
                'instructions': '1. Patient fasting\n2. IV contrast administration\n3. Venous phase primary',
            },
            # MR Protocols
            {
                'code': 'MR_BRAIN_WO',
                'name': 'MRI Brain Without Contrast',
                'modality': 'MR',
                'body_part': 'Head',
                'requires_contrast': False,
                'priority': 10,
                'clinical_keywords': ['headache', 'seizure', 'stroke', 'dizziness'],
                'description': 'Standard brain MRI without contrast',
                'instructions': '1. T1 sagittal\n2. T2 axial\n3. FLAIR axial\n4. DWI/ADC',
            },
            {
                'code': 'MR_KNEE_L',
                'name': 'MRI Left Knee',
                'modality': 'MR',
                'body_part': 'Knee',
                'laterality': 'LEFT',
                'requires_contrast': False,
                'priority': 30,
                'clinical_keywords': ['knee pain', 'left knee', 'meniscus', 'ACL'],
                'description': 'MRI of the left knee',
                'instructions': '1. Knee coil\n2. T1, T2, PD sequences\n3. Coronal, sagittal, axial',
            },
            # X-Ray Protocols
            {
                'code': 'XR_CHEST_2V',
                'name': 'Chest X-Ray 2 Views',
                'modality': 'XR',
                'body_part': 'Chest',
                'requires_contrast': False,
                'priority': 5,
                'clinical_keywords': ['cough', 'fever', 'chest pain', 'pneumonia'],
                'description': 'PA and Lateral chest radiograph',
                'instructions': '1. PA view - full inspiration\n2. Lateral view',
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
            if created:
                self.stdout.write(f'  ✓ Created protocol: {protocol.code}')
        
        return protocols

    def create_exams(self, facilities, modalities):
        exams_data = []
        
        # Generate 20 sample exams
        patient_names = [
            'Ahmed Mohammed', 'Fatima Ali', 'Mohammed Hassan',
            'Sara Abdullah', 'Ali Ahmed', 'Noura Salem'
        ]
        
        procedures = {
            'CT': ['CT Head', 'CT Chest', 'CT Abdomen', 'CT Spine'],
            'MR': ['MRI Brain', 'MRI Spine', 'MRI Knee', 'MRI Shoulder'],
            'XR': ['Chest X-Ray', 'Knee X-Ray', 'Hand X-Ray', 'Spine X-Ray'],
        }
        
        for i in range(20):
            modality = random.choice(modalities)
            facility = random.choice(facilities)
            
            exam_date = timezone.now() - timedelta(days=random.randint(0, 30))
            
            exam_data = {
                'accession_number': f'ACC{2024}{str(i+1).zfill(6)}',
                'order_id': f'ORD{str(i+1).zfill(6)}',
                'mrn': f'MRN{str(random.randint(100000, 999999))}',
                'patient_name': random.choice(patient_names),
                'patient_gender': random.choice(['M', 'F']),
                'facility': facility,
                'modality': modality,
                'procedure_name': random.choice(procedures.get(modality.code, ['Unknown Procedure'])),
                'exam_datetime': exam_date,
                'scheduled_datetime': exam_date - timedelta(hours=2),
                'status': random.choice(['SCHEDULED', 'COMPLETED', 'COMPLETED', 'COMPLETED']),
                'clinical_history': random.choice([
                    'Patient presents with acute onset headache',
                    'Chest pain and shortness of breath',
                    'Abdominal pain, right lower quadrant',
                    'Chronic knee pain, left side',
                    'Follow-up imaging for known condition',
                ]),
                'reason_for_exam': 'Diagnostic imaging',
                'technologist': random.choice(['Tech A', 'Tech B', 'Tech C']),
            }
            
            exams_data.append(exam_data)
        
        exams = []
        for data in exams_data:
            exam, created = Exam.objects.get_or_create(
                accession_number=data['accession_number'],
                defaults=data
            )
            exams.append(exam)
            if created:
                self.stdout.write(f'  ✓ Created exam: {exam.accession_number}')
        
        return exams
