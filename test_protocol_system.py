"""
Test Protocol System
اختبار شامل لنظام البروتوكولات
"""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()

from django.contrib.auth import get_user_model
from apps.core.models import Exam, Facility, Modality
from apps.protocols.models import ProtocolTemplate, ProtocolAssignment
from apps.protocols.services.assignment import protocol_assignment_service
from apps.protocols.services.suggestion import protocol_suggestion_service
from apps.core.deeplinks.generator import deeplink_generator

User = get_user_model()


class ProtocolSystemTester:
    """
    Test the protocol system end-to-end
    """
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
    
    def run_all_tests(self):
        """Run all tests"""
        print("🧪 Testing Protocol System")
        print("=" * 50)
        
        self.test_data_exists()
        self.test_protocol_suggestion()
        self.test_protocol_assignment()
        self.test_deep_link_generation()
        self.test_bulk_operations()
        
        print("\n" + "=" * 50)
        print(f"✅ Passed: {self.passed}")
        print(f"❌ Failed: {self.failed}")
        print("=" * 50)
    
    def test_data_exists(self):
        """Test that initial data exists"""
        print("\n📊 Test 1: Check Initial Data")
        
        try:
            facilities = Facility.objects.count()
            modalities = Modality.objects.count()
            protocols = ProtocolTemplate.objects.count()
            exams = Exam.objects.count()
            users = User.objects.count()
            
            print(f"  Facilities: {facilities}")
            print(f"  Modalities: {modalities}")
            print(f"  Protocols: {protocols}")
            print(f"  Exams: {exams}")
            print(f"  Users: {users}")
            
            assert facilities > 0, "No facilities found"
            assert modalities > 0, "No modalities found"
            assert protocols > 0, "No protocols found"
            assert exams > 0, "No exams found"
            assert users > 0, "No users found"
            
            print("  ✅ PASSED: All data exists")
            self.passed += 1
        
        except AssertionError as e:
            print(f"  ❌ FAILED: {str(e)}")
            self.failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            self.failed += 1
    
    def test_protocol_suggestion(self):
        """Test protocol suggestion system"""
        print("\n🤖 Test 2: Protocol Suggestion System")
        
        try:
            # Get a radiologist
            radiologist = User.objects.filter(role='RADIOLOGIST').first()
            if not radiologist:
                raise Exception("No radiologist found")
            
            # Get an exam without protocol
            exam = Exam.objects.filter(
                protocol_assignment__isnull=True
            ).first()
            
            if not exam:
                raise Exception("No unassigned exams found")
            
            print(f"  Testing with exam: {exam.accession_number}")
            print(f"  Modality: {exam.modality.code}")
            print(f"  Procedure: {exam.procedure_name}")
            
            # Get suggestions
            suggestions = protocol_suggestion_service.suggest_protocols(
                exam=exam,
                radiologist=radiologist,
                max_suggestions=5
            )
            
            print(f"  Got {len(suggestions)} suggestions:")
            for suggestion in suggestions[:3]:
                print(f"    {suggestion.rank}. {suggestion.protocol.code} - Score: {suggestion.score:.2f}")
            
            assert len(suggestions) > 0, "No suggestions returned"
            assert suggestions[0].rank == 1, "Top suggestion not ranked 1"
            
            print("  ✅ PASSED: Suggestion system works")
            self.passed += 1
        
        except AssertionError as e:
            print(f"  ❌ FAILED: {str(e)}")
            self.failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            self.failed += 1
    
    def test_protocol_assignment(self):
        """Test protocol assignment"""
        print("\n📋 Test 3: Protocol Assignment")
        
        try:
            radiologist = User.objects.filter(role='RADIOLOGIST').first()
            exam = Exam.objects.filter(
                protocol_assignment__isnull=True
            ).first()
            
            if not exam:
                print("  ⚠️  SKIPPED: No unassigned exams")
                return
            
            # Get a protocol
            protocol = ProtocolTemplate.objects.filter(
                modality=exam.modality,
                is_active=True
            ).first()
            
            if not protocol:
                raise Exception("No matching protocol found")
            
            print(f"  Assigning protocol: {protocol.code}")
            print(f"  To exam: {exam.accession_number}")
            
            # Assign protocol
            assignment = protocol_assignment_service.assign_protocol(
                exam=exam,
                protocol=protocol,
                assigned_by=radiologist,
                assignment_method='MANUAL',
                notes='Test assignment'
            )
            
            print(f"  Assignment ID: {assignment.id}")
            print(f"  Status: {assignment.status}")
            
            # Verify
            exam.refresh_from_db()
            assert hasattr(exam, 'protocol_assignment'), "Assignment not created"
            assert exam.protocol_assignment.protocol == protocol, "Wrong protocol assigned"
            
            print("  ✅ PASSED: Protocol assignment works")
            self.passed += 1
        
        except AssertionError as e:
            print(f"  ❌ FAILED: {str(e)}")
            self.failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            self.failed += 1
    
    def test_deep_link_generation(self):
        """Test deep link generation"""
        print("\n🔗 Test 4: Deep Link Generation")
        
        try:
            exam = Exam.objects.first()
            
            print(f"  Generating deep link for: {exam.accession_number}")
            
            # Generate protocol deep link
            link = deeplink_generator.generate_protocol_link(
                exam_id=str(exam.id),
                accession_number=exam.accession_number,
                mrn=exam.mrn,
                facility_code=exam.facility.code
            )
            
            print(f"  Link generated: {link[:80]}...")
            
            assert 'token=' in link, "No token in link"
            assert exam.accession_number in link or 'token=' in link, "Invalid link format"
            
            print("  ✅ PASSED: Deep link generation works")
            self.passed += 1
        
        except AssertionError as e:
            print(f"  ❌ FAILED: {str(e)}")
            self.failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            self.failed += 1
    
    def test_bulk_operations(self):
        """Test bulk protocol operations"""
        print("\n📦 Test 5: Bulk Operations")
        
        try:
            # Test activate/deactivate
            protocols = ProtocolTemplate.objects.filter(is_active=True)[:3]
            count = protocols.count()
            
            print(f"  Testing with {count} protocols")
            
            # Deactivate
            protocols.update(is_active=False)
            inactive_count = ProtocolTemplate.objects.filter(
                id__in=protocols.values_list('id', flat=True),
                is_active=False
            ).count()
            
            assert inactive_count == count, "Deactivation failed"
            print(f"  ✓ Deactivated {inactive_count} protocols")
            
            # Reactivate
            protocols.update(is_active=True)
            active_count = ProtocolTemplate.objects.filter(
                id__in=protocols.values_list('id', flat=True),
                is_active=True
            ).count()
            
            assert active_count == count, "Reactivation failed"
            print(f"  ✓ Reactivated {active_count} protocols")
            
            print("  ✅ PASSED: Bulk operations work")
            self.passed += 1
        
        except AssertionError as e:
            print(f"  ❌ FAILED: {str(e)}")
            self.failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            self.failed += 1


def main():
    """Main test runner"""
    tester = ProtocolSystemTester()
    tester.run_all_tests()


if __name__ == '__main__':
    main()