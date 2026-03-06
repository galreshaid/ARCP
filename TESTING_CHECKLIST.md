# 🧪 Protocol Module Testing Checklist

## 🚀 Quick Start Commands

```bash
# 1. Setup and activate environment
chmod +x setup_and_run.sh
./setup_and_run.sh

# 2. Load initial test data
python manage.py load_initial_data

# 3. Run automated tests
python test_protocol_system.py

# 4. Start development server
python manage.py runserver
```

---

## ✅ Manual Testing Checklist

### Part 1: Admin Panel - Basic Operations

#### 1.1 Login to Admin
- [ ] Navigate to: `http://localhost:8000/admin/`
- [ ] Login with superuser credentials
- [ ] Verify dashboard loads

#### 1.2 View Facilities
- [ ] Go to **Core** → **Facilities**
- [ ] Verify 3 facilities appear (MAIN, BRANCH1, CLINIC)
- [ ] Click on one facility
- [ ] Check **Exam count** shows number with link
- [ ] Check **User count** displays correctly

#### 1.3 View Modalities
- [ ] Go to **Core** → **Modalities**
- [ ] Verify modalities: CT, MR, XR, US, NM
- [ ] Click on CT
- [ ] Check **Exam count** link works
- [ ] Check **Protocol count** link works

#### 1.4 View Exams
- [ ] Go to **Core** → **Exams**
- [ ] Verify ~20 sample exams appear
- [ ] Check columns display:
  - Accession number
  - Patient name
  - MRN
  - Modality
  - Status badges (colored)
- [ ] Click on an exam
- [ ] Verify all fields are populated
- [ ] Check **Related Records** section shows protocol/QC/contrast links

---

### Part 2: Protocol Management

#### 2.1 View Protocols
- [ ] Go to **Protocols** → **Protocol Templates**
- [ ] Verify ~6 sample protocols appear
- [ ] Check **Usage Badge** is colored
- [ ] Check **Assignment count** shows links

#### 2.2 Create New Protocol
- [ ] Click **Add Protocol Template**
- [ ] Fill required fields:
  ```
  Code: TEST_CT_HEAD
  Name: Test CT Head Protocol
  Modality: CT
  Body Part: Head
  ```
- [ ] Set **Priority**: 50
- [ ] Set **Is Active**: ✓
- [ ] Add **Clinical Keywords**: `["test", "demo"]`
- [ ] Click **Save**
- [ ] Verify protocol appears in list

#### 2.3 Edit Protocol
- [ ] Click on a protocol to edit
- [ ] Change **Description**
- [ ] Add more **Clinical Keywords**
- [ ] Click **Save**
- [ ] Verify changes saved

#### 2.4 Bulk Operations - Activate/Deactivate
- [ ] Select 2-3 protocols (checkboxes)
- [ ] Choose **Actions** → **Deactivate selected protocols**
- [ ] Click **Go**
- [ ] Verify success message
- [ ] Check protocols show **Is Active** = False
- [ ] Select same protocols
- [ ] Choose **Actions** → **Activate selected protocols**
- [ ] Verify they're active again

#### 2.5 Bulk Operation - Set as Default
- [ ] Select ONE protocol
- [ ] Choose **Actions** → **Set as default protocol**
- [ ] Click **Go**
- [ ] Verify **Is Default** = True
- [ ] Check other protocols with same modality/body part are NOT default

#### 2.6 Bulk Operation - Duplicate
- [ ] Select a protocol
- [ ] Choose **Actions** → **Duplicate selected protocols**
- [ ] Click **Go**
- [ ] Verify new protocol appears with `_COPY` suffix
- [ ] Verify **Usage Count** = 0 for copy

#### 2.7 Export Protocols
- [ ] Select 2-3 protocols
- [ ] Choose **Actions** → **Export protocols to CSV**
- [ ] Click **Go**
- [ ] Verify CSV file downloads
- [ ] Open CSV and check data is correct

---

### Part 3: Protocol Assignment

#### 3.1 Manual Assignment
- [ ] Go to **Protocols** → **Protocol Assignments**
- [ ] Click **Add Protocol Assignment**
- [ ] Select an **Exam** (without protocol)
- [ ] Select a matching **Protocol** (same modality)
- [ ] Set **Assignment Method**: Manual
- [ ] Add **Assignment Notes**: "Test assignment"
- [ ] Click **Save**
- [ ] Verify assignment appears in list

#### 3.2 Bulk Assignment (Custom View)
- [ ] Go to **Protocol Templates**
- [ ] Select ONE protocol (e.g., CT_HEAD_NC)
- [ ] Choose **Actions** → **Bulk assign to exams**
- [ ] Click **Go**
- [ ] Verify custom page opens showing:
  - Protocol details at top
  - List of candidate exams (same modality, no protocol)
- [ ] Click **Select All**
- [ ] Verify all checkboxes checked
- [ ] Click **Deselect All**
- [ ] Verify all unchecked
- [ ] Manually select 2-3 exams
- [ ] Click **Assign Protocol to Selected Exams**
- [ ] Confirm the popup
- [ ] Verify success message
- [ ] Go to **Protocol Assignments** list
- [ ] Verify assignments were created

#### 3.3 View Assignment Details
- [ ] Click on an assignment
- [ ] Check all fields:
  - Exam link (clickable)
  - Protocol link (clickable)
  - Assigned by
  - Status badge (colored)
  - Method badge (with icon)
- [ ] Click **Exam** link → should open exam page
- [ ] Go back
- [ ] Click **Protocol** link → should open protocol page

#### 3.4 Assignment Actions
- [ ] In assignment list, select an assignment
- [ ] Choose **Actions** → **Mark as acknowledged**
- [ ] Click **Go**
- [ ] Verify status changed to ACKNOWLEDGED

---

### Part 4: Protocol Suggestions (API Testing)

#### 4.1 Test Suggestion API
Using browser or Postman:

- [ ] Login first (to get session)
- [ ] GET: `http://localhost:8000/api/protocols/suggestions/?exam_id=<EXAM_ID>`
  - Replace `<EXAM_ID>` with an actual exam UUID from admin
- [ ] Verify response contains:
  ```json
  {
    "exam_id": "...",
    "suggestions": [
      {
        "protocol": {...},
        "score": 0.85,
        "rank": 1,
        "reasoning": {...}
      }
    ]
  }
  ```
- [ ] Check suggestions are sorted by rank
- [ ] Verify reasoning includes factors like:
  - body_part_match
  - keyword_score
  - usage_score

---

### Part 5: Import/Export

#### 5.1 Import Protocols from CSV
- [ ] Create a CSV file (use `data/sample_protocols.csv` as template)
- [ ] Add 1-2 test protocols
- [ ] Run command:
  ```bash
  python manage.py import_protocols test_protocols.csv --dry-run
  ```
- [ ] Verify dry-run shows what would be created
- [ ] Run actual import:
  ```bash
  python manage.py import_protocols test_protocols.csv
  ```
- [ ] Check success message
- [ ] Go to Admin → Protocol Templates
- [ ] Verify new protocols appear

#### 5.2 Export Protocols to CSV
- [ ] Run command:
  ```bash
  python manage.py export_protocols output.csv
  ```
- [ ] Verify file created
- [ ] Open CSV
- [ ] Check all protocols exported with correct data

#### 5.3 Export with Filters
- [ ] Export CT protocols only:
  ```bash
  python manage.py export_protocols ct_protocols.csv --modality CT
  ```
- [ ] Verify only CT protocols in file
- [ ] Export active protocols only:
  ```bash
  python manage.py export_protocols active.csv --active-only
  ```
- [ ] Verify only active protocols exported

---

### Part 6: Deep Links

#### 6.1 Generate Deep Link (via Admin Action)
- [ ] Go to **Core** → **Exams**
- [ ] Select 1-2 exams
- [ ] Choose **Actions** → **Generate deep links for selected exams**
- [ ] Click **Go**
- [ ] Check success message
- [ ] Copy link from Django logs or console

#### 6.2 Test Deep Link (API)
- [ ] Login to API first
- [ ] Use generated link from above
- [ ] Access: `http://localhost:8000/api/protocols/deeplink/1/?token=<TOKEN>`
- [ ] Verify response contains:
  - Exam details
  - Existing assignment (if any)
  - Suggestions list

---

### Part 7: Edge Cases & Error Handling

#### 7.1 Try Invalid Operations
- [ ] Try to assign protocol with different modality than exam
  - Should fail with error message
- [ ] Try to duplicate protocol without selecting any
  - Should show validation error
- [ ] Try to bulk assign with multiple protocols selected
  - Should show error: "Select only ONE protocol"

#### 7.2 Try Empty States
- [ ] Create a new exam without protocol
- [ ] Verify "No protocol assigned" shows in exam detail
- [ ] Create protocol for modality with no exams
- [ ] Try bulk assign → should show "No candidate exams"

---

## 🎯 Success Criteria

### ✅ All checks must pass:

1. **Data Integrity**
   - All facilities, modalities, protocols, exams created
   - Relationships properly linked

2. **Admin Interface**
   - All pages load without errors
   - Badges and colors display correctly
   - Links between entities work
   - Bulk operations execute successfully

3. **Protocol System**
   - Suggestions return relevant protocols
   - Assignments create properly
   - Deep links generate and validate

4. **Import/Export**
   - CSV import creates protocols correctly
   - CSV export generates complete data
   - Filters work as expected

5. **Error Handling**
   - Invalid operations show clear error messages
   - System prevents data inconsistencies

---

## 📊 Performance Checks

- [ ] Admin pages load in < 2 seconds
- [ ] Suggestion API responds in < 500ms
- [ ] Bulk assignment of 50 exams completes in < 5 seconds
- [ ] Export of 100 protocols completes in < 3 seconds

---

## 🐛 Known Issues / Notes

_Document any issues found during testing here_

---

## ✅ Final Sign-Off

- [ ] All critical functionality works
- [ ] No console errors in browser
- [ ] No Django errors in terminal
- [ ] Ready to proceed to Contrast Module

**Tested by:** _________________
**Date:** _________________
**Notes:** _________________