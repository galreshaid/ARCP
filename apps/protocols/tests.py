from datetime import date, timedelta

from django.contrib.auth.models import Group
from django.template import Context, Template
from django.core import mail
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.constants import UserRole
from apps.core.models import Exam, Facility, Modality, Procedure
from apps.hl7_core.senders.orr_sender import build_exam_orm, build_protocol_assignment_orr
from apps.protocols.models import AssignmentStatus, ProtocolAssignment, ProtocolComment, ProtocolTemplate
from apps.protocols.services.suggestion import protocol_suggestion_service
from apps.protocols.views import _build_assignment_timeline
from apps.users.models import User, UserNotification


class ProtocolFormattingTemplateTests(SimpleTestCase):
    def test_protocol_note_lines_renders_clean_bullets(self):
        rendered = Template(
            "{% load protocol_formatting %}"
            "{% for line in notes|protocol_note_lines %}[{{ line }}]{% endfor %}"
        ).render(
            Context(
                {
                    "notes": "* First step\n- Second step\nThird step",
                }
            )
        )

        self.assertEqual(rendered, "[First step][Second step][Third step]")

    def test_suggestion_reasoning_lines_renders_readable_labels(self):
        rendered = Template(
            "{% load protocol_formatting %}"
            "{% for line in reasoning|suggestion_reasoning_lines %}[{{ line }}]{% endfor %}"
        ).render(
            Context(
                {
                    "reasoning": {
                        "procedure_match": True,
                        "procedure_name_score": 0.9,
                        "body_part_match": True,
                        "keyword_score": 0.42,
                        "priority_score": 0.75,
                    },
                }
            )
        )

        self.assertEqual(
            rendered,
            "[Exact procedure match][Procedure name match 90%][Body region match][Keyword overlap 42%][Priority-ranked]",
        )


class ProtocolSuggestionApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email='admin@example.com',
            password='password123',
            username='admin',
            first_name='Admin',
            last_name='User',
        )
        self.facility = Facility.objects.create(
            code='RKF',
            name='Royal Clinic',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='CT',
            name='Computed Tomography',
            is_active=True,
        )
        self.procedure = Procedure.objects.create(
            code='CSKUH',
            name='CT Head',
            modality=self.modality,
            body_region='Head',
            is_active=True,
        )
        self.exam = Exam.objects.create(
            accession_number='RKF-206315379',
            order_id='RKF-206315379',
            mrn='412345678',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CSKUH',
            procedure_name='CT HEAD',
            patient_name='HAMAD TEST HAMAD ALTEST',
            status='SCHEDULED',
        )
        self.head_protocol = ProtocolTemplate.objects.create(
            code='CT_HEAD_STANDARD',
            name='CT Head Standard',
            modality=self.modality,
            facility=self.facility,
            procedure=self.procedure,
            body_part='HEAD',
            body_region='Head',
            is_active=True,
            priority=10,
        )
        ProtocolTemplate.objects.create(
            code='CT-CHEST-PE',
            name='CT Chest Pulmonary Embolism',
            modality=self.modality,
            facility=self.facility,
            body_region='CHEST',
            is_active=True,
            priority=5,
            is_default=True,
        )

    def test_suggestions_response_includes_exam_summary(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('protocols:suggestion-list'),
            {'exam_id': str(self.exam.id)},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['exam']['accession_number'], self.exam.accession_number)
        self.assertEqual(payload['exam']['id'], str(self.exam.id))
        self.assertIn('manual_protocols', payload)
        self.assertTrue(payload['manual_protocols'])
        self.assertIn('match_percent', payload['suggestions'][0])
        self.assertLessEqual(payload['suggestions'][0]['match_percent'], 100)

    def test_head_exam_does_not_rank_chest_protocol_above_head_protocol(self):
        suggestions = protocol_suggestion_service.suggest_protocols(
            exam=self.exam,
            radiologist=self.user,
            max_suggestions=5,
        )

        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0].protocol.code, 'CT_HEAD_STANDARD')
        self.assertNotIn('CT-CHEST-PE', [item.protocol.code for item in suggestions[:1]])

    def test_exact_procedure_name_match_ranks_specific_protocol_first(self):
        renal_exam = Exam.objects.create(
            accession_number='RKF-RENAL-001',
            order_id='RKF-RENAL-001',
            mrn='499999999',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTRENAL',
            procedure_name='CT Renal',
            patient_name='Renal Test Patient',
            clinical_history='Left flank pain',
            status='SCHEDULED',
        )
        ProtocolTemplate.objects.create(
            code='CT_ABDOMEN_GENERIC',
            name='Routine CT Abdomen and Pelvis',
            modality=self.modality,
            facility=self.facility,
            body_region='ABDOMEN',
            is_active=True,
            priority=1,
            is_default=True,
        )
        exact_name_protocol = ProtocolTemplate.objects.create(
            code='CT_RENAL_PROTOCOL',
            name='CT Renal',
            modality=self.modality,
            facility=self.facility,
            is_active=True,
            priority=50,
        )

        suggestions = protocol_suggestion_service.suggest_protocols(
            exam=renal_exam,
            radiologist=self.user,
            max_suggestions=5,
        )

        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0].protocol, exact_name_protocol)

    def test_behavior_learning_prioritizes_historically_selected_protocol(self):
        learned_protocol = ProtocolTemplate.objects.create(
            code='CT_ABD_BEHAVIOR',
            name='CT Abdomen Personalized',
            modality=self.modality,
            facility=self.facility,
            body_region='ABDOMEN',
            is_active=True,
            priority=95,
        )
        ProtocolTemplate.objects.create(
            code='CT_ABD_DEFAULT',
            name='CT Abdomen Default',
            modality=self.modality,
            facility=self.facility,
            body_region='ABDOMEN',
            is_active=True,
            is_default=True,
            priority=1,
        )

        for index in range(8):
            historical_exam = Exam.objects.create(
                accession_number=f'RKF-BHV-{index:03d}',
                order_id=f'RKF-BHV-{index:03d}',
                mrn=f'BHV-MRN-{index:03d}',
                facility=self.facility,
                modality=self.modality,
                procedure_code='CTABD',
                procedure_name='CT ABDOMEN',
                patient_name=f'Behavior Patient {index}',
                clinical_history='Abdominal pain',
                status='COMPLETED',
            )
            ProtocolAssignment.objects.create(
                exam=historical_exam,
                protocol=learned_protocol,
                assigned_by=self.user,
                assignment_method='MANUAL',
                status='DONE',
                assigned_at=timezone.now() - timedelta(days=index + 1),
            )

        behavior_exam = Exam.objects.create(
            accession_number='RKF-BHV-TARGET',
            order_id='RKF-BHV-TARGET',
            mrn='BHV-MRN-TARGET',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTABD',
            procedure_name='CT ABDOMEN',
            patient_name='Behavior Target Patient',
            clinical_history='Abdominal pain follow-up',
            status='SCHEDULED',
        )

        suggestions = protocol_suggestion_service.suggest_protocols(
            exam=behavior_exam,
            radiologist=self.user,
            max_suggestions=5,
        )

        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0].protocol, learned_protocol)
        self.assertGreater(suggestions[0].reasoning.get('behavior_context_score', 0), 0)

    def test_assignment_api_creates_assignment(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('protocols:assignment-list'),
            {
                'exam_id': str(self.exam.id),
                'protocol_id': str(self.head_protocol.id),
                'assignment_method': 'AI',
                'radiologist_note': 'Assigned from protocol assignment page.',
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ProtocolAssignment.objects.count(), 1)

        assignment = ProtocolAssignment.objects.get()
        self.assertEqual(assignment.exam, self.exam)
        self.assertEqual(assignment.protocol, self.head_protocol)
        self.assertEqual(assignment.assignment_method, 'AI')
        self.assertEqual(response.json()['status'], 'PENDING')

    def test_assignment_api_returns_validation_error_for_duplicate_assignment(self):
        self.client.force_login(self.user)
        ProtocolAssignment.objects.create(
            exam=self.exam,
            protocol=self.head_protocol,
            assigned_by=self.user,
            assignment_method='AI',
        )

        response = self.client.post(
            reverse('protocols:assignment-list'),
            {
                'exam_id': str(self.exam.id),
                'protocol_id': str(self.head_protocol.id),
                'assignment_method': 'AI',
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('already has a protocol assigned', response.json()['non_field_errors'][0])


class ProtocolApiPermissionEnforcementTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(
            code='PERM',
            name='Permission Facility',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='CT',
            name='Computed Tomography',
            is_active=True,
        )
        self.procedure = Procedure.objects.create(
            code='CTPERM',
            name='CT Permission Study',
            modality=self.modality,
            body_region='Chest',
            is_active=True,
        )
        self.exam = Exam.objects.create(
            accession_number='PERM-001',
            order_id='PERM-001',
            mrn='40001',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTPERM',
            procedure_name='CT PERMISSION STUDY',
            patient_name='Permission Test Patient',
            status='SCHEDULED',
        )
        self.protocol = ProtocolTemplate.objects.create(
            code='CT_PERMISSION_PROTOCOL',
            name='CT Permission Protocol',
            modality=self.modality,
            facility=self.facility,
            procedure=self.procedure,
            body_region='Chest',
            is_active=True,
        )

        self.finance_user = User.objects.create_user(
            email='finance-perm@example.com',
            password='password123',
            username='financeperm',
            first_name='Finance',
            last_name='Perm',
            role=UserRole.FINANCE,
        )
        self.finance_user.groups.add(Group.objects.get(name='Finance'))
        self.finance_user.facilities.add(self.facility)

        self.technologist_user = User.objects.create_user(
            email='tech-perm@example.com',
            password='password123',
            username='techperm',
            first_name='Tech',
            last_name='Perm',
            role=UserRole.TECHNOLOGIST,
        )
        self.technologist_user.groups.add(Group.objects.get(name='Technologist'))
        self.technologist_user.facilities.add(self.facility)

        self.radiologist_user = User.objects.create_user(
            email='rad-perm@example.com',
            password='password123',
            username='radperm',
            first_name='Rad',
            last_name='Perm',
            role=UserRole.RADIOLOGIST,
        )
        self.radiologist_user.groups.add(Group.objects.get(name='Radiologist'))
        self.radiologist_user.facilities.add(self.facility)

    def test_finance_cannot_access_protocol_template_api(self):
        self.client.force_login(self.finance_user)

        response = self.client.get(reverse('protocols:template-list'))

        self.assertEqual(response.status_code, 403)

    def test_finance_cannot_access_protocol_suggestion_api(self):
        self.client.force_login(self.finance_user)

        response = self.client.get(
            reverse('protocols:suggestion-list'),
            {'exam_id': str(self.exam.id)},
        )

        self.assertEqual(response.status_code, 403)

    def test_technologist_can_view_templates_but_cannot_create_assignment(self):
        self.client.force_login(self.technologist_user)

        templates_response = self.client.get(reverse('protocols:template-list'))
        self.assertEqual(templates_response.status_code, 200)

        create_response = self.client.post(
            reverse('protocols:assignment-list'),
            {
                'exam_id': str(self.exam.id),
                'protocol_id': str(self.protocol.id),
                'assignment_method': 'MANUAL',
            },
        )

        self.assertEqual(create_response.status_code, 403)

    def test_radiologist_can_create_assignment_via_api(self):
        self.client.force_login(self.radiologist_user)

        response = self.client.post(
            reverse('protocols:assignment-list'),
            {
                'exam_id': str(self.exam.id),
                'protocol_id': str(self.protocol.id),
                'assignment_method': 'MANUAL',
                'radiologist_note': 'Permission test assignment',
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            ProtocolAssignment.objects.filter(
                exam=self.exam,
                protocol=self.protocol,
                assigned_by=self.radiologist_user,
            ).exists()
        )


class TechnologistProtocolViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email='tech@example.com',
            password='password123',
            username='tech',
            first_name='Tech',
            last_name='User',
        )
        self.user.role = UserRole.TECHNOLOGIST
        self.user.save(update_fields=['role'])
        self.radiologist = User.objects.create_user(
            email='radiologist-review@example.com',
            password='password123',
            username='radiologistreview',
            first_name='Radio',
            last_name='Logist',
            role=UserRole.RADIOLOGIST,
        )

        self.facility = Facility.objects.create(
            code='TECH',
            name='Technologist Facility',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='MR',
            name='Magnetic Resonance Imaging',
            is_active=True,
        )
        self.protocol = ProtocolTemplate.objects.create(
            code='MR_TEST_PROTOCOL',
            name='MR Test Protocol',
            modality=self.modality,
            facility=self.facility,
            body_region='Head',
            is_active=True,
            indications='Follow the standard workflow.',
            general_notes='Confirm patient setup before scanning.',
        )
        self.exam = Exam.objects.create(
            accession_number='TECH-001',
            order_id='TECH-001',
            mrn='20001',
            facility=self.facility,
            modality=self.modality,
            procedure_code='MRHEAD',
            procedure_name='MR HEAD',
            patient_name='Test Patient',
            status='SCHEDULED',
        )
        self.user.facilities.add(self.facility)
        self.radiologist.facilities.add(self.facility)
        self.assignment = ProtocolAssignment.objects.create(
            exam=self.exam,
            protocol=self.protocol,
            assigned_by=self.radiologist,
            assignment_method='MANUAL',
            status='PENDING',
        )

    def test_confirmation_requires_explicit_checkbox(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('protocoling-technologist-view', args=[self.exam.id]),
            {
                'technologist_note': 'Ready to proceed.',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please confirm you reviewed the protocol before saving.')
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, AssignmentStatus.PENDING)

    def test_confirmation_updates_assignment_status(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('protocoling-technologist-view', args=[self.exam.id]),
            {
                'technologist_note': 'Patient prepared and protocol confirmed.',
                'comment': 'Table setup completed.',
                'confirm_review': '1',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('?confirmed=1', response['Location'])

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, AssignmentStatus.ACKNOWLEDGED)
        self.assertEqual(self.assignment.technologist_note, 'Patient prepared and protocol confirmed.')
        self.assertEqual(self.assignment.acknowledged_by, self.user)
        self.assertEqual(self.assignment.comments.count(), 2)
        self.assertTrue(self.assignment.comments.filter(author=self.user).exists())
        self.assertTrue(self.assignment.comments.filter(author__isnull=True, author_role='SYSTEM').exists())
        self.assertEqual(UserNotification.objects.count(), 1)
        notification = UserNotification.objects.get()
        self.assertEqual(notification.recipient, self.radiologist)
        self.assertEqual(notification.sender, self.user)
        self.assertFalse(notification.is_read)
        self.assertTrue(notification.target_url.endswith(f'/protocoling/review/{self.exam.id}/'))
        self.assertTrue(notification.email_sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.radiologist.email])

    def test_timeline_keeps_original_assignment_first_after_reassignment(self):
        original_created_at = self.assignment.created_at
        self.assignment.assigned_at = timezone.now()
        self.assignment.modifications = {
            'history': [
                {
                    'at': self.assignment.assigned_at.isoformat(),
                    'by': 'Tech User',
                    'summary': 'Protocol changed from MR_TEST_PROTOCOL to MR_TEST_PROTOCOL_V2.',
                }
            ]
        }
        self.assignment.save(update_fields=['assigned_at', 'modifications'])

        events = _build_assignment_timeline(self.assignment)

        self.assertEqual(events[0]['event_type'], 'assignment')
        self.assertEqual(events[0]['occurred_at'], original_created_at)
        self.assertEqual(events[0]['body'], 'MR_TEST_PROTOCOL - Initial assignment')

    def test_radiologist_update_after_acknowledgement_notifies_technologist(self):
        self.assignment.status = AssignmentStatus.ACKNOWLEDGED
        self.assignment.acknowledged_by = self.user
        self.assignment.acknowledged_at = timezone.now()
        self.assignment.save(update_fields=['status', 'acknowledged_by', 'acknowledged_at'])

        self.client.force_login(self.radiologist)

        response = self.client.post(
            reverse('protocoling-radiologist-review', args=[self.exam.id]),
            {
                'manual_protocol_id': str(self.protocol.id),
                'ai_selected': '0',
                'radiologist_note': 'Updated after technologist confirmation.',
                'comment': 'Please re-review before proceeding.',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, AssignmentStatus.PENDING)
        self.assertIsNone(self.assignment.acknowledged_by)
        self.assertIsNone(self.assignment.acknowledged_at)
        self.assertTrue(
            self.assignment.comments.filter(
                author__isnull=True,
                author_role='SYSTEM',
                message__icontains='Technologist follow-up is required',
            ).exists()
        )
        self.assertEqual(UserNotification.objects.count(), 1)
        notification = UserNotification.objects.get()
        self.assertEqual(notification.recipient, self.user)
        self.assertEqual(notification.sender, self.radiologist)
        self.assertFalse(notification.is_read)
        self.assertTrue(notification.target_url.endswith(f'/protocoling/technologist/{self.exam.id}/'))
        self.assertTrue(notification.email_sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_technologist_can_send_direct_message_from_review_page(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('protocoling-technologist-view', args=[self.exam.id]),
            {
                'form_action': 'send_message',
                'message_recipient_id': str(self.radiologist.id),
                'message_title': 'Need radiologist input',
                'message_body': 'Please review this protocol before the exam proceeds.',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('?message_sent=1', response['Location'])
        self.assertEqual(UserNotification.objects.count(), 1)
        notification = UserNotification.objects.get()
        self.assertEqual(notification.recipient, self.radiologist)
        self.assertEqual(notification.sender, self.user)
        self.assertEqual(notification.category, 'DIRECT_MESSAGE')
        self.assertEqual(notification.title, 'Need radiologist input')
        self.assertIn('Please review this protocol', notification.message)
        self.assertTrue(notification.target_url.endswith(f'/protocoling/review/{self.exam.id}/'))
        self.assertTrue(
            self.assignment.comments.filter(
                author=self.user,
                message__icontains='Direct message sent to',
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.radiologist.email])


class RoleRestrictedProtocolReviewTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email='admin-review@example.com',
            password='password123',
            username='adminreview',
            first_name='Admin',
            last_name='Review',
        )
        self.facility = Facility.objects.create(
            code='ROLE',
            name='Role Facility',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='CT',
            name='Computed Tomography',
            is_active=True,
        )
        self.protocol = ProtocolTemplate.objects.create(
            code='ROLE_PROTOCOL',
            name='Role Protocol',
            modality=self.modality,
            facility=self.facility,
            body_region='Abdomen',
            is_active=True,
        )
        self.exam = Exam.objects.create(
            accession_number='ROLE-001',
            order_id='ROLE-001',
            mrn='30001',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTABD',
            procedure_name='CT ABDOMEN',
            patient_name='Role Test Patient',
            status='SCHEDULED',
        )
        ProtocolAssignment.objects.create(
            exam=self.exam,
            protocol=self.protocol,
            assigned_by=self.admin_user,
            assignment_method='MANUAL',
            status='PENDING',
        )
        self.radiologist = User.objects.create_user(
            email='radiologist@example.com',
            password='password123',
            username='radiologist',
            first_name='Radio',
            last_name='Logist',
            role=UserRole.RADIOLOGIST,
        )
        self.radiologist.facilities.add(self.facility)
        self.technologist = User.objects.create_user(
            email='technologist@example.com',
            password='password123',
            username='technologist',
            first_name='Tech',
            last_name='Nologist',
            role=UserRole.TECHNOLOGIST,
        )
        self.technologist.facilities.add(self.facility)

    def test_radiologist_cannot_open_technologist_review(self):
        self.client.force_login(self.radiologist)

        response = self.client.get(
            reverse('protocoling-technologist-view', args=[self.exam.id]),
        )

        self.assertEqual(response.status_code, 403)

    def test_technologist_cannot_open_radiologist_review(self):
        self.client.force_login(self.technologist)

        response = self.client.get(
            reverse('protocoling-radiologist-review', args=[self.exam.id]),
        )

        self.assertEqual(response.status_code, 403)

    def test_radiologist_can_send_direct_message_from_review_page(self):
        self.client.force_login(self.radiologist)

        response = self.client.post(
            reverse('protocoling-radiologist-review', args=[self.exam.id]),
            {
                'form_action': 'send_message',
                'message_recipient_id': str(self.technologist.id),
                'message_title': 'Need technologist review',
                'message_body': 'Please open the technologist review and confirm readiness.',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('?message_sent=1', response['Location'])
        self.assertEqual(UserNotification.objects.count(), 1)
        notification = UserNotification.objects.get()
        self.assertEqual(notification.recipient, self.technologist)
        self.assertEqual(notification.sender, self.radiologist)
        self.assertEqual(notification.category, 'DIRECT_MESSAGE')
        self.assertEqual(notification.title, 'Need technologist review')
        self.assertIn('Please open the technologist review', notification.message)
        self.assertTrue(notification.target_url.endswith(f'/protocoling/technologist/{self.exam.id}/'))
        self.assertTrue(
            ProtocolComment.objects.filter(
                assignment__exam=self.exam,
                message__icontains='Direct message sent to',
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.technologist.email])


class HL7OrderMessageBuilderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email='hl7@example.com',
            password='password123',
            username='hl7user',
            first_name='HL7',
            last_name='User',
        )
        self.facility = Facility.objects.create(
            code='RKF',
            name='Royal Clinic',
            hl7_facility_id='10000000001811',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='CT',
            name='Computed Tomography',
            is_active=True,
        )
        self.protocol = ProtocolTemplate.objects.create(
            code='CT_STROKE_PROTOCOL',
            name='CT Stroke Protocol',
            modality=self.modality,
            facility=self.facility,
            body_region='Head',
            is_active=True,
        )
        self.exam = Exam.objects.create(
            accession_number='21860732',
            order_id='RKF-206439661',
            mrn='447121911',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CACDB',
            procedure_name='CT ANGIO AORTIC ARCH AND CAROTID BOTH',
            patient_name='ASKARIA ALSWAIDANI',
            patient_dob=date(1952, 2, 10),
            patient_gender='F',
            clinical_history='History:stroke',
            status='SCHEDULED',
            metadata={
                'hl7_source_facility': 'RKF',
                'hl7_order_number': 'RKF-206439661',
                'hl7_accession_number': '21860732',
            },
        )
        self.assignment = ProtocolAssignment.objects.create(
            exam=self.exam,
            protocol=self.protocol,
            assigned_by=self.user,
            assignment_method='AI',
            radiologist_note='Proceed with stroke pathway.',
            assignment_notes='Auto-built for integration.',
        )

    def test_build_exam_orm_includes_accession_and_order_numbers(self):
        message_id, message = build_exam_orm(self.exam)

        self.assertTrue(message_id.startswith('ORM'))
        self.assertIn('MSH|^~\\&|AIP|RKF|RIS|RKF|', message)
        self.assertIn('ORC|NW|RKF-206439661|21860732^^^21860732|', message)
        self.assertIn('OBR|1|RKF-206439661^RKF-206439661|21860732|CACDB^CT ANGIO AORTIC ARCH AND CAROTID BOTH|', message)

    def test_build_protocol_assignment_orr_includes_accession_and_protocol_note(self):
        message_id, message = build_protocol_assignment_orr(self.assignment)

        self.assertTrue(message_id.startswith('ORR'))
        self.assertIn('MSH|^~\\&|AIP|RKF|RIS|RKF|', message)
        self.assertIn('||ORR^O02|', message)
        self.assertIn('ORC|SC|RKF-206439661|21860732^^^21860732|', message)
        self.assertIn('OBR|1|RKF-206439661^RKF-206439661|21860732|CACDB^CT ANGIO AORTIC ARCH AND CAROTID BOTH|', message)
        self.assertIn('NTE|1|L|Protocol Assigned: CT_STROKE_PROTOCOL^CT Stroke Protocol', message)
        self.assertIn('NTE|2|L|Radiologist Note: Proceed with stroke pathway.', message)
