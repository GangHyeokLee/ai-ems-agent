"""Offline candidate workbench: evidence grouping, versioning, interchange and model isolation."""
import copy
import csv
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.kr import review_candidates as review
from pipelines.kr import candidates as cand
from pipelines.kr.sources import load_map_graph, sha256
from pipelines.kr.topology import analyze
from test_kr_topology_pipeline import fixture_map, write, STATION, run_cli


@pytest.fixture
def sample(tmp_path):
    source=write(tmp_path/'map.json',fixture_map())
    graph=load_map_graph(source)
    topology=analyze(graph)
    rows=cand.substation_candidates(graph,topology['bus_component'])
    nearby,_=cand.nearby_bus_candidates(graph,topology['bus_component'],200)
    rows+=nearby
    for i,r in enumerate(rows):r['candidate_id']=f'KR-TOPO-{i:05d}'
    return graph,rows


def payload(sample,version='a'*64):
    return review.build_review(*sample,version)


def decision(p,**extra):
    return dict(review.decision_identity(p['candidates'][0]),status='needs_information',notes='단선도 필요',
                evidence_sources='docs/SLD-001.pdf',reviewed_at='2026-10-26T15:00:00+09:00',
                re_review_required=False,**extra)


def test_selection_coordinates_distance_and_original_geometry(sample):
    graph,rows=sample;before=copy.deepcopy(graph)
    p=payload(sample);r=p['candidates'][0]
    assert len(p['candidates'])==1
    assert p['summary']['distinct_component_pairs']==1
    assert r['distance_target']=='bus_to_bus' and r['distance_m']==pytest.approx(50,abs=.02)
    assert r['bus_ids']==[row['a_bus_id'] for row in rows if row['candidate_type']==review.KIND]+[row['b_bus_id'] for row in rows if row['candidate_type']==review.KIND]
    assert p['lines']==graph.lines and p['buses']==graph.buses
    assert graph==before
    assert r['a_inside_polygon'] and r['b_inside_polygon']
    assert r['status']=='unreviewed' and not r['re_review_required']


def test_station_evidence_not_collapsed_and_keys_ignore_display_ids(sample):
    graph,rows=sample
    original=next(r for r in rows if r['candidate_type']==review.KIND)
    duplicate=copy.deepcopy(original);duplicate['substation_osm_id']='way/901';duplicate['candidate_id']='other'
    for bus in graph.buses.values():
        if 'way/900' in bus.get('substation_candidates',[]):bus['substation_candidates'].append('way/901')
    graph.layers['substations'].append(dict(osm_id='way/901',name='Second',geometry=STATION))
    rows.append(duplicate)
    p=payload(sample)
    assert len(p['candidates'])==2 and p['summary']['distinct_component_pairs']==1
    assert p['summary']['substations']==2
    assert len({r['review_key'] for r in p['candidates']})==2
    assert all(r['pair_evidence_count']==2 and r['pair_rank']==1 for r in p['candidates'])
    for r in rows:r['candidate_id']='renumbered'
    rows.reverse()
    assert [r['review_key'] for r in payload(sample)['candidates']]==[r['review_key'] for r in p['candidates']]


def test_deterministic_output_priority_and_missing_tags(sample):
    a=payload(sample);b=payload(sample)
    assert review.render_html(a)==review.render_html(b)
    r=a['candidates'][0]
    assert r['voltage_tags']=='missing' and 'voltage_tags_missing' in r['warnings']
    assert r['priority_score']==max(0,sum(r['score_parts'].values()))
    assert a['summary']['score_policy']['meaning'].endswith('확률이 아님')


@pytest.mark.parametrize('distance,points',[(0,30),(50,30),(50.01,24),(100,24),(200,16),(500,8),(501,0)])
def test_priority_distance_buckets(distance,points):
    score,band,parts=review.priority(distance,[True,True],'consistent',[1,1],2,True,[])
    assert parts['distance']==points and score==points+60


def test_polygon_boundary_holes_and_missing_geometry(sample):
    assert review.covered_by_polygon(127.009,36,STATION)
    assert not review.covered_by_polygon(127,36,STATION)
    hole=[[127.0095,35.9995],[127.0105,35.9995],[127.0105,36.0005],[127.0095,36.0005],[127.0095,35.9995]]
    polygon=copy.deepcopy(STATION);polygon['coordinates'].append(hole)
    assert not review.covered_by_polygon(127.01,36,polygon)
    assert review.covered_by_polygon(127.0095,36,polygon)
    sample[0].layers['substations'][0]['geometry']=None
    p=payload(sample);r=p['candidates'][0]
    assert r['a_inside_polygon'] is None and 'substation_polygon_missing' in r['warnings']
    assert not p['stations']['way/900']['polygon_available']


@pytest.mark.parametrize('tag,expected',[('', 'missing'),('154000;345000','consistent'),('345000','conflict'),('NaN','conflict'),('154 kV','conflict')])
def test_voltage_tags(tag,expected):
    assert review.voltage_tag({'voltage':tag},154)==expected


def test_keys_change_with_inputs_but_preserve_evidence_identity(sample):
    old=payload(sample);new=payload(sample,'b'*64)
    assert old['candidates'][0]['identity_key']==new['candidates'][0]['identity_key']
    assert old['candidates'][0]['review_key']!=new['candidates'][0]['review_key']
    document=review.export_decisions(old,[decision(old)])
    result=review.import_decisions(new,document)
    assert not result['decisions'] and result['stale_records'][0]['re_review_required']
    reexport=review.export_decisions(new,[],result['stale_records'])
    assert review.import_decisions(new,reexport)==result


def test_interchange_round_trip_all_statuses_and_untrusted_notes(sample):
    p=payload(sample)
    for status in review.STATES:
        d=decision(p);d['status']=status;d['notes']='</script><img src=x onerror="alert(1)">';d['evidence_sources']='javascript:alert(1)'
        doc=review.export_decisions(p,[d])
        assert review.import_decisions(p,json.loads(json.dumps(doc)))['decisions']==[d]


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(schema_version='unsupported'),
    lambda d:d.update(input_hash='invalid'),
    lambda d:d['decisions'][0].update(status='approved'),
    lambda d:d['decisions'][0].update(bus_ids=['unknown','other']),
    lambda d:d['decisions'][0].update(input_hash='b'*64),
    lambda d:d['decisions'][0].update(review_key='KR-TOPO-1'),
    lambda d:d['decisions'][0].update(reviewed_at='2026-10-26'),
    lambda d:d['decisions'][0].update(notes={}),
    lambda d:d['decisions'][0].update(re_review_required=True),
    lambda d:d['decisions'].append(copy.deepcopy(d['decisions'][0])),
])
def test_invalid_interchange_rejected_atomically(sample,mutation):
    p=payload(sample);d=review.export_decisions(p,[decision(p)]);mutation(d)
    with pytest.raises(ValueError):review.import_decisions(p,d)
    assert all(r['status']=='unreviewed' for r in p['candidates'])


def test_unknown_current_key_is_rejected(sample):
    p=payload(sample);d=decision(p);d['identity_key']='f'*64;d['review_key']=review.review_key(d['input_hash'],d['identity_key'])
    with pytest.raises(ValueError,match='존재하지 않는'):review.export_decisions(p,[d])
    malformed=decision(p);malformed['status']={}
    with pytest.raises(ValueError,match='상태'):review.export_decisions(p,[malformed])


def test_html_escapes_external_names_and_no_network(sample):
    attack='</script><script>window.PWNED=1</script><img src=x onerror=alert(1)>'
    sample[0].layers['substations'][0]['name']=attack
    p=payload(sample);html=review.render_html(p)
    assert attack not in html and '\\u003c/script\\u003e' in html
    assert "connect-src 'none'" in html and '<script src=' not in html
    data=html.split('<script id="review-data" type="application/json">')[1].split('</script>')[0]
    assert json.loads(data)['candidates'][0]['substation_name']==attack


@pytest.mark.parametrize('field,value',[('a_latitude',0),('a_component',999),('distance_m',300),('a_voltage_kv',345),('distance_target','a_bus_to_line_route')])
def test_stale_analysis_row_is_rejected(sample,field,value):
    r=next(r for r in sample[1] if r['candidate_type']==review.KIND);r[field]=value
    with pytest.raises(ValueError):payload(sample)


def test_cli_reproducibility_protected_inputs_and_decisions(tmp_path):
    analysis=tmp_path/'analysis';run_cli(tmp_path,analysis)
    source=tmp_path/'map.json';output=tmp_path/'review'
    argv=['--map',str(source),'--analysis-dir',str(analysis),'--output-dir',str(output)]
    originals={p:sha256(p) for p in [source,analysis/'summary.json',analysis/'connection_candidates.csv']}
    assert review.main(argv)==0
    sentinel=output/'review_decisions.json';sentinel.write_text('user-owned')
    before={p.name:sha256(p) for p in output.iterdir()}
    assert review.main(argv)==0
    assert before=={p.name:sha256(p) for p in output.iterdir()}
    assert originals=={p:sha256(p) for p in originals}
    html=output/'index.html';html.unlink();html.symlink_to(source)
    assert review.main(argv)==2 and sha256(source)==originals[source]
    html.unlink()
    candidates=analysis/'connection_candidates.csv';csv_content=candidates.read_text();candidates.write_text(csv_content.splitlines()[0]+'\n')
    assert review.main(argv)==2  # truncated/mixed analysis artifacts must not become a zero-candidate report
    candidates.write_text(csv_content)
    source.write_text(source.read_text()+' ')
    assert review.main(argv)==2


@pytest.fixture(scope='module')
def real_payload():
    project=ROOT.parent/'korea-grid-poc';analysis=ROOT/'outputs/kr_topology'
    if not (project/'outputs/map/kr_grid_preview.json').is_file() or not (analysis/'summary.json').is_file():
        pytest.skip('real Korea data/analysis absent')
    graph=load_map_graph(project/'outputs/map/kr_grid_preview.json')
    with (analysis/'connection_candidates.csv').open() as f:rows=list(csv.DictReader(f))
    return graph,review.build_review(graph,rows,'e'*64)


def test_real_384_candidates_367_pairs_165_stations_and_original_routes(real_payload):
    graph,p=real_payload;s=p['summary']
    assert (s['candidates'],s['distinct_component_pairs'],s['substations'])==(384,367,165)
    assert s['extra_station_evidence']==17 and s['duplicate_pair_count']==17
    assert len({r['review_key'] for r in p['candidates']})==384
    assert set(r['pair_rank'] for r in p['candidates'])==set(range(1,368))
    assert p['lines']==sorted(graph.lines,key=lambda l:l['line_id'])
    assert s['ac_pf_readiness']=='NOT_READY' and s['warnings'].get('bus_outside_substation_polygon',0)==0
    assert all(r['status']=='unreviewed' for r in p['candidates'])


def test_real_inputs_unchanged_and_no_ems_imports(real_payload,tmp_path):
    project=ROOT.parent/'korea-grid-poc';analysis=ROOT/'outputs/kr_topology'
    protected=[project/'networks/KR/base.nc',project/'outputs/map/kr_grid_preview.json',project/'data/processed/osm_power_objects.json',ROOT/'data/grids/kr-osm-transmission-map.json']
    before={p:sha256(p) for p in protected}
    args=SimpleNamespace(map=protected[1],osm_objects=protected[2],analysis_dir=analysis,output_dir=tmp_path/'review')
    summary=review.run(args)
    assert summary['ac_pf_readiness']=='NOT_READY'
    assert before=={p:sha256(p) for p in protected}
    # The generated viewer has no API endpoints or app imports; only pipeline code changed.
    html=(tmp_path/'review/index.html').read_text()
    assert '/api/' not in html and 'fetch(' not in html
    catalog=(ROOT/'src/ai_ems/grid_catalog.py').read_text()
    assert 'NOT_READY' in catalog
