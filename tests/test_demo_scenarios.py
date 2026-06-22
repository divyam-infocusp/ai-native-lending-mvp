"""
Tests for the demo scenario harness — the per-application `demo_scenario` tag that
drives the mock bureau/OCR so every origination path is triggerable from the UI.
"""
from lending.adapters.bureau import pull_bureau
from lending.adapters.demo_scenarios import ScenarioBureauHarness
from lending.adapters.ocr_mock import make_reflective_ocr_extractor
from lending.los import Applicant, Application, ApplicationRepository, make_engine


def _repo_with(scenario: str):
    repo = ApplicationRepository(make_engine())
    app = Application(applicant=Applicant(full_name="Ravi Kumar"),
                      features={"demo_scenario": scenario})
    repo.save(app)
    return repo, app.application_id


def test_scenario_bureau_low_cibil_knocks_out():
    repo, app_id = _repo_with("low_cibil")
    report = pull_bureau(ScenarioBureauHarness(repo), app_id)
    assert report.has_record and report.score == 600        # below the CIBIL floor


def test_scenario_bureau_high_dti_obligations():
    repo, app_id = _repo_with("high_dti")
    report = pull_bureau(ScenarioBureauHarness(repo), app_id)
    assert report.total_monthly_obligations == 60_000.0


def test_scenario_bureau_thin_file_recovers_on_repull():
    repo, app_id = _repo_with("thin_file")
    harness = ScenarioBureauHarness(repo)
    first = pull_bureau(harness, app_id)
    second = pull_bureau(harness, app_id)
    assert first.has_record is False                        # → UW_EXCEPTION
    assert second.has_record is True                        # re-assessment proceeds


def test_scenario_bureau_clean_is_default():
    repo, app_id = _repo_with("clean")
    report = pull_bureau(ScenarioBureauHarness(repo), app_id)
    assert report.has_record and report.score == 780


def test_ocr_doc_mismatch_perturbs_name():
    repo, app_id = _repo_with("doc_mismatch")
    extract = make_reflective_ocr_extractor(repo)
    id_name = extract(app_id, "identity_proof")["name"]["value"]
    f16_name = extract(app_id, "form16")["name"]["value"]
    assert id_name == "Ravi Kumar"
    assert f16_name != id_name                              # cross-source name mismatch → KYC_EXCEPTION
