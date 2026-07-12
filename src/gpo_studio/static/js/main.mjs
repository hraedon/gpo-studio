import {state,$,$$} from './state.mjs';
import {toast} from './api.mjs';
import {loadList,renderList,renderSettings,loadHistory} from './render.mjs';
import {openGpo,openSetting,openLink,openFilter,openWmi,openEstate,openFork,syncSettingForm,initForms} from './forms.mjs';
import {checkAdmx,initAdmx} from './admx.mjs';
import {initDiff,loadDiffSelectors} from './diff.mjs';

$$(".tab").forEach(tab=>tab.onclick=()=>{$$(".tab").forEach(x=>x.classList.toggle("active",x===tab));$$(".panel").forEach(x=>x.classList.toggle("active",x.id===`panel-${tab.dataset.tab}`));if(tab.dataset.tab==="history")loadHistory();if(tab.dataset.tab==="diff")loadDiffSelectors();if(tab.dataset.tab==="admx")checkAdmx()});
$$(".filter-row .chip").forEach(chip=>chip.onclick=()=>{$$(".filter-row .chip").forEach(x=>x.classList.toggle("active",x===chip));state.side=chip.dataset.side;renderSettings()});
$("#new-gpo").onclick=()=>openGpo();$("#empty-new").onclick=()=>openGpo();$("#edit-metadata").onclick=()=>openGpo(true);$("#add-setting").onclick=()=>openSetting();$("#add-link").onclick=()=>openLink();$("#add-filter").onclick=()=>openFilter();$("#edit-wmi").onclick=()=>openWmi();$("#import-estate").onclick=()=>openEstate();$("#fork-gpo").onclick=()=>openFork();$("#search").oninput=renderList;
for(const name of ["side","action","registry_type"])$("#setting-form")[name].onchange=syncSettingForm;
initForms();initAdmx();initDiff();loadList().catch(error=>toast(error.message));
