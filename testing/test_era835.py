from app.insurance.era835 import parse


def test_parses_final_835_claim_totals():
    payload = (b"ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260919*1200*^*00501*000000001*0*T*:~"
               b"GS*HP*SENDER*RECEIVER*20260919*1200*1*X*005010X221A1~ST*835*0001~"
               b"BPR*I*125.50*C*ACH*CCP*01*111*DA*222*151*333*20260919~"
               b"N1*PR*Example Payer*PI*99999~CLP*PATIENT-CONTROL*1*200*125.5*74.5*12*PAYER-CONTROL*11~SE*5*0001~GE*1*1~IEA*1*000000001~")
    result = parse(payload)
    assert result["payment_cents"] == 12_550
    assert result["payer"] == "Example Payer"
    assert result["claims"] == [{"patient_control_number": "PATIENT-CONTROL", "status_code": "1", "billed_cents": 20_000,
                                  "insurer_paid_cents": 12_550, "patient_responsibility_cents": 7_450,
                                  "payer_claim_control_number": "PAYER-CONTROL"}]
