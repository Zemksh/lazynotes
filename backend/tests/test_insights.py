import json
import time
import uuid
from dataclasses import asdict

import pytest

from app import db
from app.insights import build_insights, concept_groups, transcription_insights
from app.schemas import BulletGroup, Notes, Result, Section, Segment
from app.tasks import job_lock, job_path
from app.transcription import Diagnostics

TOKEN = 'a' * 64
AUTH = {'Authorization': f'Bearer {TOKEN}'}


def section(text, heading='Topic', sid='s1'):
    return Section(heading=heading, bullets=[text], source_segment_ids=[sid])


def result(mock=False):
    return Result(job_id=str(uuid.uuid4()), mock=mock, duration_seconds=20,
        notes=Notes(title='Test', sections=[section('ATP powers transport.', 'Energy'),
            section('Transport moves molecules.', 'Movement', 's2')]),
        transcript=[Segment(id='s1', start=0, end=10, text='ATP powers transport.'),
                    Segment(id='s2', start=10, end=20, text='Transport moves molecules.')])


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Insights must not call a model or transcribe audio')
    monkeypatch.setattr('app.generation.httpx.post', forbidden)
    monkeypatch.setattr('app.transcription.whisper_model', forbidden)


def test_actual_flags_counts_mapping_and_no_mutation():
    r = result()
    before = r.model_dump_json()
    diagnostics = [Diagnostics('s1', -1.5, 0.1, 1), Diagnostics('s2', -0.1, 0.1, 1),
                   Diagnostics('unknown', -2, 1, 3), Diagnostics('s1', -1.5, 0.1, 1)]
    data = build_insights(r, diagnostics)
    reliability = data.transcription
    assert reliability.status == 'available'
    assert reliability.method == 'rule_based'
    assert reliability.analyzed_passages == 2
    assert reliability.flagged_passages == reliability.affected_sections == 1
    assert reliability.passages[0].text == r.transcript[0].text
    assert reliability.passages[0].headings == ['Energy']
    assert r.model_dump_json() == before
    assert not {'faithfulness', 'accuracy', 'confidence', 'probability'} & data.model_dump().keys()


def test_partial_missing_nonfinite_and_mock_are_not_claimed_reliable():
    r = result()
    assert transcription_insights(r, []).status == 'unavailable'
    report = transcription_insights(r, [Diagnostics('s1', -.1, .1, 1),
                                        Diagnostics('s2', float('nan'), .1, 1)])
    assert report.status == 'partial'
    assert report.analyzed_passages == 1
    assert report.flagged_passages == 0
    mock = transcription_insights(result(mock=True), [Diagnostics('s1', -2, 1, 3)])
    assert mock.status == 'mock' and mock.analyzed_passages == 0 and not mock.passages


@pytest.mark.parametrize('diagnostic', [Diagnostics('s1', -1.01, 0, 0),
    Diagnostics('s1', 0, .61, 0), Diagnostics('s1', 0, 0, 2.41)])
def test_existing_rule_triggers_are_preserved(diagnostic):
    assert transcription_insights(result(), [diagnostic]).flagged_passages == 1


def test_cluster_related_sections_without_supplied_labels():
    sections = [section('Membrane transport diffusion concentration gradient molecules', 'First'),
                section('Membrane transport diffusion concentration gradient molecules', 'Second'),
                section('Stars galaxies astronomy telescope orbit', 'Third'),
                section('Stars galaxies astronomy telescope orbit', 'Fourth')]
    groups = concept_groups(sections)
    assert [g.section_indices for g in groups.groups] == [[0, 1], [2, 3]]
    assert groups == concept_groups(sections)
    assert any('diffusion' in term for term in groups.groups[0].terms)
    assert any('astronomy' in term for term in groups.groups[1].terms)
    assert sorted(i for g in groups.groups for i in g.section_indices) == [0, 1, 2, 3]


def test_no_fixed_cluster_count_and_identical_sections_are_safe():
    identical = [section('diffusion molecules gradient') for _ in range(5)]
    assert len(concept_groups(identical).groups) == 1
    distinct = [section(text, heading=text) for text in ('banana', 'galaxies', 'algebra')]
    assert len(concept_groups(distinct).groups) == 3


def test_inline_source_ids_do_not_dominate_terms_or_split_related_notes():
    sections = [section('Membrane diffusion molecules gradient. ' + ' '.join(f's{i:05}' for i in range(30)),
                        heading='Transport', sid='s00001'),
                section('Molecules diffusion membrane gradient. ' + ' '.join(f's{i:05}' for i in range(30, 60)),
                        heading='Movement', sid='s00030'),
                section('Stars galaxies astronomy telescope orbit', heading='Astronomy')]
    groups = concept_groups(sections).groups
    assert [g.section_indices for g in groups] == [[0, 1], [2]]
    assert all(not term.startswith('s000') for g in groups for term in g.terms)


def test_old_insights_cache_is_replaced_without_changing_notes(client):
    r, folder = ready_job()
    old = build_insights(r, []).model_dump()
    old['version'] = 1
    (folder / 'insights.json').write_text(json.dumps(old))
    before = (folder / 'result.json').read_text()
    response = client.get(f'/jobs/{r.job_id}/insights', headers=AUTH)
    assert response.status_code == 200
    assert response.json()['version'] == 2
    assert (folder / 'result.json').read_text() == before


def test_empty_singleton_stopwords_and_mixed_zero_vectors():
    assert concept_groups([]).status == 'insufficient_text'
    empty = section('the and of', heading='the')
    assert concept_groups([empty]).status == 'insufficient_text'
    assert concept_groups([empty]).ungrouped_sections == 1
    singleton = concept_groups([section('Diffusion')])
    assert len(singleton.groups) == 1 and singleton.groups[0].section_indices == [0]
    mixed = concept_groups([empty, section('Diffusion')])
    assert mixed.ungrouped_sections == 1 and mixed.groups[0].section_indices == [1]


def test_nested_details_and_tables_are_included():
    from app.insights import section_text
    from app.schemas import Comparison
    s = Section(heading='Transport', bullets=[BulletGroup(text='Parent', children=['Qualification'])],
                comparison=Comparison(columns=['Energy', 'Example'], rows=[['ATP', 'Pump']]),
                source_segment_ids=['s1'])
    assert all(word in section_text(s) for word in ('Qualification', 'Energy', 'ATP', 'Pump'))


def ready_job():
    r = result()
    db.create_job(r.job_id, r.job_id, TOKEN, 'Test')
    for state in ('transcribing', 'preparing', 'ready'):
        db.transition(r.job_id, state)
    folder = job_path(r.job_id)
    folder.mkdir(parents=True)
    (folder / 'result.json').write_text(r.model_dump_json())
    (folder / 'diagnostics.json').write_text(json.dumps([asdict(Diagnostics('s1', -2, 0, 1))]))
    (folder / 'reliability.json').write_text('{"faithfulness": "PRIVATE_INTERNAL_REPORT"}')
    return r, folder


def test_insights_endpoint_is_authorized_cached_and_separate(client, monkeypatch):
    r, folder = ready_job()
    url = f'/jobs/{r.job_id}/insights'
    before = (folder / 'result.json').read_text()
    assert client.get(url).status_code == 401
    assert client.get(url, headers={'Authorization': f'Bearer {"b" * 64}'}).status_code == 404
    response = client.get(url, headers=AUTH)
    assert response.status_code == 200
    assert response.json()['transcription']['flagged_passages'] == 1
    assert 'PRIVATE_INTERNAL_REPORT' not in response.text
    assert 'insights' not in client.get(f'/jobs/{r.job_id}/result', headers=AUTH).json()
    assert (folder / 'result.json').read_text() == before
    def forbidden(*args):
        raise AssertionError('The cached result must be reused')
    monkeypatch.setattr('app.main.build_insights', forbidden)
    assert client.get(url, headers=AUTH).json() == response.json()
    with job_lock(r.job_id):
        assert client.get(url, headers=AUTH).status_code == 409
    with db.connection() as connection:
        connection.execute('UPDATE jobs SET expires_at=? WHERE id=?', (time.time() - 1, r.job_id))
    assert client.get(url, headers=AUTH).status_code == 410


@pytest.mark.parametrize('raw', [None, 'invalid json', '[{"segment_id": []}]', '[]'])
def test_legacy_missing_diagnostics_still_clusters(client, raw):
    r, folder = ready_job()
    path = folder / 'diagnostics.json'
    if raw is None:
        path.unlink()
    else:
        path.write_text(raw)
    response = client.get(f'/jobs/{r.job_id}/insights', headers=AUTH)
    assert response.status_code == 200
    assert response.json()['transcription']['status'] == 'unavailable'
    assert response.json()['concepts']['groups']


def test_not_ready_and_deleted_cannot_get_insights(client):
    jid = str(uuid.uuid4())
    db.create_job(jid, jid, TOKEN, 'Test')
    url = f'/jobs/{jid}/insights'
    assert client.get(url, headers=AUTH).status_code == 409
    db.transition(jid, 'deleted')
    assert client.get(url, headers=AUTH).status_code == 410
