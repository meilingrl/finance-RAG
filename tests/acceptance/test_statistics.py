"""Breaks caught: treating repeats as questions and unpaired per-question bootstrap."""
import importlib
import unittest

class StatisticsTests(unittest.TestCase):
    def module(self):
        try:return importlib.import_module('formal_src.statistics')
        except ModuleNotFoundError:self.fail('paired source-cluster statistics not implemented')

    def test_cluster_resampling_keeps_correlated_questions_together(self):
        rows=[]
        for q,source,value in [('a','one',1),('b','one',1),('c','one',1),('d','two',-1)]:
            for repeat in (1,2,3):rows.append({'question_id':q,'source_id':source,'repeat':repeat,'difference':value})
        out=self.module().paired_cluster_interval(rows,iterations=1000,seed=42)
        self.assertEqual(out['question_count'],4)
        self.assertEqual(out['source_count'],2)
        self.assertEqual(out['effect'],0.5)
        self.assertEqual(out['source_macro_effect'],0.0)
        self.assertEqual(out['ci_9875'],[-1.0,1.0])
        self.assertEqual(out,self.module().paired_cluster_interval(rows,iterations=1000,seed=42))

    def test_rounds_are_averaged_before_question_mean(self):
        rows=[{'question_id':'a','source_id':'s','repeat':r,'difference':v} for r,v in [(1,1),(2,0),(3,1)]]
        out=self.module().paired_cluster_interval(rows,iterations=30,seed=1)
        self.assertEqual(out['question_count'],1)
        self.assertAlmostEqual(out['effect'],2/3)
        self.assertAlmostEqual(out['ci_95'][0],2/3)

    def test_conflicting_sources_or_duplicate_rounds_rejected(self):
        row={'question_id':'a','source_id':'s','repeat':1,'difference':1}
        for rows in [[row,row],[row,{**row,'repeat':2,'source_id':'other'}]]:
            with self.assertRaises(ValueError):self.module().paired_cluster_interval(rows,iterations=20,seed=2)

    def test_four_main_contrasts_average_rounds_and_ignore_diagnostic(self):
        rows=[]
        for retriever,condition,values in [('bm25','E1',[0]),('bm25','E2',[1,0,1]),('minilm','E2',[1,1,1]),('bm25','E3',[1,1,1]),('bm25','E4',[0,0,0])]:
            for repeat,value in enumerate(values,1):
                rows.append(dict(question_id='q',source_id='s',partition='main',retriever=retriever,condition=condition,model_role='local' if condition=='E1' else 'primary',repeat=repeat,uem=value))
        rows.append(dict(question_id='other',source_id='x',partition='diagnostic',retriever='bm25',condition='E2',repeat=1,uem=1))
        method=getattr(self.module(),'main_comparisons',None)
        self.assertTrue(callable(method),'four main contrast implementation missing')
        out=method(rows,iterations=30,seed=1)
        for key,want in [('P1',2/3),('P2',1/3),('P3',1/3),('P4',-2/3)]:
            self.assertAlmostEqual(out[key]['effect'],want)
            self.assertEqual(out[key]['question_count'],1)

    def test_main_comparisons_never_mix_secondary_model_or_drop_rounds(self):
        rows=[]
        for retriever,condition,role,values in [('bm25','E1','local',[0]),('bm25','E2','primary',[1,0,1]),('minilm','E2','primary',[1,1,1]),('bm25','E3','primary',[1,1,1]),('bm25','E4','primary',[0,0,0])]:
            for repeat,value in enumerate(values,1):
                rows.append(dict(question_id='q',source_id='s',partition='main',retriever=retriever,condition=condition,model_role=role,repeat=repeat,uem=value))
        secondary={**rows[1],'model_role':'secondary','uem':0}
        try:out=self.module().main_comparisons(rows+[secondary],iterations=10,seed=1)
        except ValueError:self.fail('secondary model collided with primary main comparison')
        self.assertAlmostEqual(out['P1']['effect'],2/3)
        truncated=[r for r in rows if not(r['retriever']=='minilm' and r['repeat']==3)]
        with self.assertRaises(ValueError):self.module().main_comparisons(truncated,iterations=10,seed=1)

if __name__=='__main__':unittest.main()
