BEGIN;
CREATE TABLE IF NOT EXISTS medicare_fee_schedule (
    code TEXT NOT NULL, description TEXT NOT NULL,
    allowed_cents INTEGER NOT NULL CHECK (allowed_cents > 0),
    source_reference TEXT NOT NULL, effective_from TEXT NOT NULL,
    PRIMARY KEY (code, effective_from)
);
INSERT INTO medicare_fee_schedule (code, description, allowed_cents, source_reference, effective_from) VALUES
    ('99203', 'Office o/p new low 30 min', 11960, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('99204', 'Office o/p new mod 45 min', 18049, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('99205', 'Office o/p new hi 60 min', 24151, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('99213', 'Office o/p est low 20 min', 9633, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('99214', 'Office o/p est mod 30 min', 13741, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('99215', 'Office o/p est hi 40 min', 19513, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('64612', 'Chemodenervation of muscle(s); facial/trigeminal/cervical (as applicable)', 14502, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('J0585', 'Injection, OnabotulinumtoxinA (Botox), per unit', 651, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('95911', 'Nerve Conduction Studies (9–10 studies)', 22107, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('95886', 'Needle EMG, each extremity with related paraspinal areas', 9997, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('98006', 'Synchronous telemedicine E/M service', 10555, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('11104', 'Punch bx skin single lesion', 12195, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('11105', 'Punch bx skin ea sep/addl', 6115, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('96372', 'Ther/proph/diag inj sc/im', 1547, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13',)
    ('95983', 'Alys brn npgt prgrmg 15 min', 5289, 'Clinic-approved Medicare allowed fee schedule, 2026-11-13', '2026-11-13')
ON CONFLICT (code, effective_from) DO UPDATE SET
    description = EXCLUDED.description, allowed_cents = EXCLUDED.allowed_cents,
    source_reference = EXCLUDED.source_reference;
COMMIT;
