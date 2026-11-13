from scripts.benchlib.runner import reuse_configuration,checkpoint_configuration


def test_unrelated_code_and_environment_do_not_invalidate_result():
    old={'system':'prompt','parameters':None,'advisor':'advisor','policy':'protected','max_tokens':None,
         'implementation':{'analytics.py':'old'},'environment':{'python':'old'},'max_steps':30,'tools':['old']}
    new={**old,'implementation':{'analytics.py':'new'},'environment':{'python':'new'},'max_steps':150,'tools':['new']}
    assert reuse_configuration(old)==reuse_configuration(new)
    assert checkpoint_configuration(old)!=checkpoint_configuration(new) # changed tool protocol cannot resume blindly


def test_prompt_and_model_parameters_still_invalidate_result():
    old={'system':'prompt','parameters':None}
    assert reuse_configuration(old)!=reuse_configuration({**old,'system':'new prompt'})
    assert reuse_configuration(old)!=reuse_configuration({**old,'parameters':{'temperature':0.2}})
