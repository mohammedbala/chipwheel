import json
from pathlib import Path
import tempfile
import unittest
from campaign import allocation, commit, totals, atomic, physical_ok, connect, measurement_current, qualified, write_state, read, sha
from campaign_designs import CANDIDATES, uart
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'test'))

def report(n, failed=0):
    return dict(final=True,checked=n,passed=n-failed,failed=failed,seconds=1,cycles=n*100,coverage={'uart':n})

class AccountingTests(unittest.TestCase):
    def test_resume_duplicate_and_crash_accounting(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);connect(folder).close()
            commit(folder,'calibration','A',0,report(7))
            commit(folder,'calibration','A',0,report(7))
            self.assertEqual(totals(folder)['checked'],7)
            # A crashed worker's non-final progress is never credited.
            with self.assertRaises(ValueError):commit(folder,'calibration','A',7,dict(report(5),final=False))
            self.assertEqual(totals(folder)['checked'],7)
            commit(folder,'calibration','A',7,report(5))
            self.assertEqual(totals(folder)['checked'],12)
            self.assertEqual(totals(folder)['coverage'],{'uart':12})
            with self.assertRaises(ValueError):commit(folder,'calibration','A',13,report(1))

    def test_exact_allocation_and_reallocation(self):
        self.assertEqual(allocation(list('ABCD'),100000,{}),dict.fromkeys('ABCD',25000))
        q=allocation(list('BCD'),100000,{'A':123,'B':10000})
        self.assertEqual(sum(q.values())+123,100000)
        self.assertLessEqual(max(q.values())-min(q.values()),1)
        self.assertEqual(allocation(list('BC'),100,{'A':20,'B':60}),{'B':60,'C':20})

    def test_unknown_physical_never_qualifies(self):
        self.assertFalse(physical_ok({}))
        x=dict(status='passed',routing='passed',precheck='passed',gate_regression='passed',area_um2=100,setup_slack_ns=0,hold_slack_ns=.1)
        self.assertTrue(physical_ok(x))
        for key in ['area_um2','setup_slack_ns','hold_slack_ns']:
            self.assertFalse(physical_ok(dict(x,**{key:None})))
        self.assertFalse(physical_ok(dict(x,setup_slack_ns=-.1)))
        for invalid in (float('nan'),float('inf'),True):
            self.assertFalse(physical_ok(dict(x,area_um2=invalid)))

    def test_changed_design_and_netlist_cannot_reuse_results(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);candidate=folder/'candidates/A';candidate.mkdir(parents=True)
            netlist=candidate/'mapped-netlist.v';netlist.write_text('original mapped circuit')
            manifest={'candidates':{'A':{'rtl_sha256':'new-rtl'}},'input_hashes':{'src/config.json':'config','.tools/pdk-lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib':'library'},'pdk_revision':'pdk'}
            atomic(folder/'manifest.json',manifest)
            old={'status':'passed','rtl_sha256':'old-rtl','config_sha256':'config','library_sha256':'library','netlist_sha256':sha(netlist),'pdk_revision':'pdk'}
            atomic(candidate/'qualification.json',old)
            self.assertFalse(qualified(folder,'A',manifest['candidates']['A']))
            self.assertFalse(measurement_current(folder,'A',old,'mapping'))
            current=dict(old,rtl_sha256='new-rtl')
            self.assertTrue(measurement_current(folder,'A',current,'mapping'))
            self.assertTrue(measurement_current(folder,'A',current,'physical'))
            self.assertFalse(measurement_current(folder,'A',dict(current,pdk_revision='other'),'physical'))
            self.assertFalse(measurement_current(folder,'A',dict(current,config_sha256='other'),'mapping'))
            netlist.write_text('changed mapped circuit')
            self.assertFalse(measurement_current(folder,'A',current,'mapping'))

    def test_forecast_weights_remaining_stage_workload(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            atomic(folder/'manifest.json',{'id':'forecast','revision':'test','stage_budgets':{'calibration':100,'stress':100}})
            r=dict(report(100),seconds=170,coverage={'uart':60,'pulse':25,'control':10,'boundary':5},category_seconds={'uart':60,'pulse':50,'control':40,'boundary':20})
            commit(folder,'calibration','A',0,r)
            write_state(folder,'running')
            state=read(folder/'state.json')
            self.assertAlmostEqual(state['eta_seconds'],300)
            self.assertEqual(state['estimate_basis'],'remaining stage mix')

    def test_fused_program_contract(self):
        for b in (3,8,4096):
            original=uart(CANDIDATES['A'],b);fused=uart(CANDIDATES['D'],b)
            self.assertEqual(len(original),9);self.assertEqual(len(fused),8)
            self.assertEqual(fused[3],0x7000+b-2)
        for value in (0,2,4097):
            with self.assertRaises(ValueError):uart(CANDIDATES['D'],value)

if __name__=='__main__':unittest.main()
