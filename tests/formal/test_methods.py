"""Breaks caught: evidence leakage, recursive expansion, lost headers and truncation."""
import importlib
import unittest


def table_unit():
    specs=[('(USD in millions)',0,0,240,10),('2018',100,20,130,30),('2019',200,20,230,30),('Revenue',0,40,60,50),('10',100,40,130,50),('12',200,40,230,50)]
    lines=[]
    for ids in [[0],[1,2],[3,4,5]]:
        cells=[{'text':specs[i][0],'bbox':list(specs[i][1:])} for i in ids]
        lines.append({'text':'\t'.join(c['text'] for c in cells),'bbox':[0,min(c['bbox'][1] for c in cells),240,max(c['bbox'][3] for c in cells)],'cells':cells})
    return {'evidence_id':'u1','doc_id':'doc','region':[0,0,250,60],'text':'\n'.join(l['text'] for l in lines),'blocks':[{'block_id':'b1','lines':lines,'bbox':[0,0,240,50],'text':'\n'.join(l['text'] for l in lines)}]}

class MethodTests(unittest.TestCase):
    def module(self,name):
        try:return importlib.import_module('formal_src.'+name)
        except ModuleNotFoundError:self.fail(name+' method is not implemented')

    def test_expansion_is_one_hop_and_preserves_common_evidence(self):
        module=self.module('evidence')
        units=[{'evidence_id':f'u{i}','doc_id':'d','text':'text'} for i in range(3)]
        edges=[{'source':'u0','target':'u1','kind':'footnote'},{'source':'u1','target':'u2','kind':'continuation'}]
        self.assertEqual(module.expand_once(['u0'],units,edges),['u0','u1'])
        with self.assertRaises(ValueError):module.expand_once(['missing'],units,edges)
        units[0]['answer']='must not enter input'
        with self.assertRaises(ValueError):module.expand_once(['u0'],units,edges)

    def test_structure_is_input_blind_and_binds_year_row_unit(self):
        module=self.module('structure');unit=table_unit()
        result=module.describe_units([unit])[0]
        node_text={n['node_id']:n['text'] for n in result['nodes']}
        relations=[(r['kind'],node_text[r['source']],node_text[r['target']]) for r in result['relations']]
        self.assertIn(('column_header','12','2019'),relations)
        self.assertIn(('row_label','12','Revenue'),relations)
        self.assertIn(('unit','12','(USD in millions)'),relations)
        unit['gold_answer']='12'
        with self.assertRaises(ValueError):module.describe_units([unit])

    def test_structure_projection_does_not_add_unselected_facts(self):
        module=self.module('structure');u=table_unit();other={**table_unit(),'evidence_id':'u2'}
        records=module.describe_units([u,other]);projected=module.project_structure(records,['u1'])
        self.assertEqual([r['evidence_id'] for r in projected],['u1'])
        self.assertTrue(all(r['source'] in {n['node_id'] for n in projected[0]['nodes']} for r in projected[0]['relations']))

    def test_long_unit_window_hits_tail_and_k4_is_supported(self):
        module=self.module('retrieval')
        class Encoder:
            def tokenize(self,text):return text.split()
            def encode(self,texts):return [[1.,0.] if 'needle' in t.split() else [0.,1.] for t in texts]
        q={'question_id':'q','doc_id':'d','question':'needle'}
        units=[{'evidence_id':'tail','doc_id':'d','text':' '.join(['filler']*300+['needle'])}]+[{'evidence_id':f'x{i}','doc_id':'d','text':'filler'} for i in range(4)]
        found=module.retrieve(q,units,{'retriever':'minilm','k':4,'window_size':192,'stride':160},encoder=Encoder())
        self.assertEqual(found['selected_ids'][0],'tail')
        self.assertEqual(found['actual_k'],4)
        self.assertGreater(found['window_counts']['tail'],1)
        other={**units[0],'doc_id':'other','evidence_id':'leak'}
        self.assertNotIn('leak',module.retrieve(q,units+[other],{'retriever':'bm25','k':4})['candidate_ids'])

    def test_formal_baseline_uses_explicit_year_column_and_abstains_on_conflict(self):
        module=self.module('baseline')
        result=module.extract('What was revenue in 2019?', [('u1','(USD in millions)\n2018\t2019\nRevenue\t10\t12')])
        self.assertEqual(result['answer'],'12')
        self.assertEqual(result['scale'],'million')
        conflict=module.extract('What was revenue in 2019?', [('u1','2018\t2019\nRevenue\t10\t12'),('u2','2018\t2019\nRevenue\t10\t99')])
        self.assertEqual(conflict['status'],'abstained')

    def test_formal_baseline_inherits_unparenthesized_explicit_unit(self):
        result=self.module('baseline').extract('What was revenue in 2019?', [('u1','USD in millions\n2018\t2019\nRevenue\t10\t12')])
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['scale'],'million')

    def test_truncation_diagnostic_is_explicit_and_not_the_main_windowed_run(self):
        class Encoder:
            def tokenize(self,text):return text.split()
            def encode(self,texts):return [[1.,0.] if 'needle' in t.split() else [0.,1.] for t in texts]
        q={'question_id':'q','doc_id':'d','question':'needle'}
        units=[{'evidence_id':'tail','doc_id':'d','text':' '.join(['filler']*300+['needle'])}]
        found=self.module('retrieval').retrieve(q,units,{'retriever':'minilm','k':1,'variant':'first_segment'},encoder=Encoder())
        self.assertEqual(found['candidate_scores']['tail'],0.0)
        self.assertTrue(found['input_truncated']['tail'])
        self.assertEqual(found['window_counts']['tail'],1)

    def test_explicit_footnotes_and_continuations_are_found_without_questions(self):
        units=[{'evidence_id':'table','doc_id':'d','text':'Customer costs(2)\t100','region':[0,0,200,40]},
               {'evidence_id':'note','doc_id':'d','text':'(2) Customer costs include fees.','region':[0,45,200,60]},
               {'evidence_id':'p1','doc_id':'d','text':'Disposals amounted to 16664','region':[0,70,200,85]},
               {'evidence_id':'p2','doc_id':'d','text':'million, mainly internet-related services.','region':[0,86,200,100]}]
        module=self.module('evidence');method=getattr(module,'build_associations',None)
        self.assertTrue(callable(method),'input-only association builder missing')
        edges=method(units)
        self.assertIn({'source':'table','target':'note','kind':'footnote'},edges)
        self.assertIn({'source':'p1','target':'p2','kind':'continuation'},edges)

    def test_footnote_relation_is_projected_only_when_both_units_are_shared(self):
        table={'evidence_id':'table','doc_id':'d','region':[0,0,200,40], 'text':'Customer costs(2)\t100',
               'blocks':[{'lines':[{'cells':[{'text':'Customer costs(2)','bbox':[0,0,100,20]},{'text':'100','bbox':[120,0,160,20]}]}]}]}
        note={'evidence_id':'note','doc_id':'d','region':[0,45,200,60], 'text':'(2) Customer costs include fees.',
              'blocks':[{'lines':[{'cells':[{'text':'(2) Customer costs include fees.','bbox':[0,45,200,60]}]}]}]}
        module=self.module('structure');records=module.describe_units([table,note])
        both=module.project_structure(records,['table','note'])
        self.assertTrue(any(r['kind']=='footnote' for row in both for r in row['relations']))
        alone=module.project_structure(records,['table'])
        self.assertFalse(any(r['kind']=='footnote' for r in alone[0]['relations']))

    def test_public_input_boundaries_reject_nested_gold_aliases(self):
        q={'question_id':'q','doc_id':'d','question':'revenue','metadata':{'gold_answer':'secret'}}
        units=[{'evidence_id':'a','doc_id':'d','text':'Revenue 10'}]
        with self.assertRaises(ValueError):self.module('retrieval').retrieve(q,units,{'retriever':'bm25','k':1})
        units[0]['metadata']={'ground_truth':'secret'}
        with self.assertRaises(ValueError):self.module('evidence').expand_once(['a'],units,[])

    def test_standalone_parenthesized_loss_is_not_a_footnote(self):
        units=[{'evidence_id':'a','doc_id':'d','text':'Net loss\t(2)','region':[0,0,100,10]},
               {'evidence_id':'b','doc_id':'d','text':'(2) Benefits include fees.','region':[0,20,100,30]}]
        self.assertEqual(self.module('evidence').build_associations(units),[])

    def test_structure_refuses_question_exposure_in_metadata(self):
        unit=table_unit();unit['metadata']={'question':'Which value?'}
        with self.assertRaises(ValueError):self.module('structure').describe_units([unit])

    def test_zero_margin_still_abstains_on_exact_conflicting_ties(self):
        result=self.module('baseline').extract('What was revenue in 2019?', [('a','2018\t2019\nRevenue\t10\t12'),('b','2018\t2019\nRevenue\t10\t99')],margin=0)
        self.assertEqual(result['status'],'abstained')

    def test_separate_tables_never_inherit_stale_headers_or_units(self):
        import copy
        unit=table_unit();other=copy.deepcopy(table_unit())
        for line in other['blocks'][0]['lines']:
            for cell in line['cells']:
                cell['bbox']=[cell['bbox'][0],cell['bbox'][1]+100,cell['bbox'][2],cell['bbox'][3]+100]
                cell['text']={'2018':'2020','2019':'2021','10':'20','12':'30','(USD in millions)':'(USD in thousands)'}.get(cell['text'],cell['text'])
            line['text']='\t'.join(c['text'] for c in line['cells'])
        unit['blocks'][0]['lines']+=other['blocks'][0]['lines']
        unit['text']='\n'.join(line['text'] for line in unit['blocks'][0]['lines'])
        record=self.module('structure').describe_units([unit])[0]
        nodes={n['node_id']:n['text'] for n in record['nodes']}
        refs=[(r['kind'],nodes[r['source']],nodes[r['target']]) for r in record['relations']]
        self.assertIn(('column_header','20','2020'),refs)
        self.assertNotIn(('column_header','20','2018'),refs)
        self.assertIn(('unit','20','(USD in thousands)'),refs)
        self.assertNotIn(('unit','20','(USD in millions)'),refs)

    def test_side_by_side_table_units_are_scoped_to_their_blocks(self):
        import copy
        for has_local in (True,False):
            unit=table_unit();right=copy.deepcopy(table_unit())
            right_lines=right['blocks'][0]['lines'] if has_local else right['blocks'][0]['lines'][1:]
            for line in right_lines:
                for cell in line['cells']:
                    cell['bbox']=[cell['bbox'][0]+300,cell['bbox'][1]+5,cell['bbox'][2]+300,cell['bbox'][3]+5]
                    cell['text']={'2018':'2020','2019':'2021','10':'20','12':'30','(USD in millions)':'(USD in thousands)'}.get(cell['text'],cell['text'])
                line['text']='\t'.join(c['text'] for c in line['cells'])
            unit['blocks'].append({'lines':right_lines})
            unit['text']='\n'.join(line['text'] for block in unit['blocks'] for line in block['lines'])
            record=self.module('structure').describe_units([unit])[0]
            nodes={n['node_id']:n['text'] for n in record['nodes']}
            refs=[(r['kind'],nodes[r['source']],nodes[r['target']]) for r in record['relations']]
            self.assertIn(('unit','10','(USD in millions)'),refs)
            self.assertNotIn(('unit','10','(USD in thousands)'),refs)
            if has_local:self.assertIn(('unit','20','(USD in thousands)'),refs)
            else:self.assertFalse(any(kind=='unit' and value=='20' for kind,value,target in refs))

if __name__=='__main__':unittest.main()
