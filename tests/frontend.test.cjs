const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const html = fs.readFileSync('app/static/index.html', 'utf8');

function load(names, globals) {
    const context = vm.createContext(globals);
    for (const name of names) {
        const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
        assert.ok(start >= 0, `Missing function ${name}`);
        const rest = source.slice(start);
        const next = rest.slice(1).search(/^(?:async )?function \w+\(/m);
        vm.runInContext(next >= 0 ? rest.slice(0, next + 1) : rest, context);
    }
    return context;
}

test('profile city accepts user input and preserves it with suggestions', () => {
    assert.match(html, /<input[^>]+id="profileCity"[^>]+list="profileCitySuggestions"/);
    const city = { value: '桂林' };
    const suggestions = { innerHTML: '', children: [], appendChild(item) { this.children.push(item); } };
    const c = load(['populateCityInput'], {
        document: { getElementById: id => id === 'profileCity' ? city : suggestions },
        uniqueValues: values => [...new Set(values)],
        createOption: value => ({ value }),
    });
    c.populateCityInput('profileCity', ['不限', '南宁'], '桂林');
    assert.equal(city.value, '桂林');
    assert.ok(suggestions.children.some(item => item.value === '南宁'));
});

test('late province response cannot replace current city suggestions', async () => {
    const input = { value: '广州' };
    const province = { value: '广东' };
    const calls = [];
    const resolvers = {};
    const c = load(['handleProfileProvinceChange'], {
        PROFILE_UNSET_OPTION: '未填写',
        document: { getElementById: id => id === 'profileProvince' ? province : input },
        getValue: id => id === 'profileProvince' ? province.value : input.value,
        fetchOptions: name => new Promise(resolve => { resolvers[name] = resolve; }),
        populateSelect: (id, values, selected) => calls.push({ id, values, selected }),
        normalizeArray: value => Array.isArray(value) ? value : [],
        showToast: () => {}, getErrorText: String,
    });
    const old = c.handleProfileProvinceChange({ target: { value: '广西' } });
    const current = c.handleProfileProvinceChange({ target: { value: '广东' } });
    resolvers['广东']({ cities: ['广州'] });
    await current;
    resolvers['广西']({ cities: ['南宁'] });
    await old;
    assert.equal(calls.length, 1);
    assert.ok(calls[0].values.includes('广州'));
});

test('server profile round trip preserves qualifications and zero years', () => {
    const c = load(['applyServerProfile'], {
        normalizeUserProfile: value => value,
        normalizeArray: value => Array.isArray(value) ? value : [],
        PROFILE_TRACKED_FIELDS: ['province'],
        isProfileFieldFilled: (value, key) => Boolean(value[key]),
        PROFILE_STORAGE_KEY: 'test', userProfile: {},
        saveStoredValue: () => {}, renderProfileForm: () => {},
        applyProfileToAnalyst: () => {}, renderProfileSurfaces: () => {},
    });
    c.applyServerProfile({ province: '广西', city: '南宁', degree: '学士', political_status: '群众', grassroots_years: 0 });
    assert.equal(c.userProfile.degree, '学士');
    assert.equal(c.userProfile.political_status, '群众');
    assert.equal(c.userProfile.grassroots_years, 0);
});

test('national shortlist identity preserves department and position codes', () => {
    const c = load(['normalizeJob', 'getJobKey'], {
        getDisplayPositionCode: job => job.position_code || '',
        firstPresent: (...values) => values.find(value => value !== undefined && value !== null),
        optionalNumber: value => value == null ? null : Number(value),
        sanitizeUserFacingText: value => String(value || ''),
        normalizePositionCode: value => String(value || ''),
        getJobDisplayTitle: job => job.position_name || '测试岗位',
        formatPublicJobFlag: value => String(value || ''),
        normalizeArray: value => Array.isArray(value) ? value : [],
        normalizeJobRiskFlags: () => [], normalizeRiskLevel: value => value || '',
    });
    const job = { province: '北京', year: 2027, exam_type: '国考', position_code: '100110001001', department_code: '001000' };
    assert.equal(c.normalizeJob(job).department_code, '001000');
    assert.notEqual(c.getJobKey(job), c.getJobKey({...job, department_code: '002000'}));
});


test('score recruitment years come from score datasets rather than job years or a fixed 2025',()=>{
    const select={value:'2025',dataset:{},options:[],appendChild(option){this.options.push(option);},set innerHTML(value){this.options=[{value:''}];}};
    const datasets=[{year:2026},{year:2024},{year:2026},{year:'invalid'}];
    const c=load(['renderScoreYearOptions'],{
        document:{getElementById:id=>id==='scoreYear'?select:{dataset:{}}},
        getScoreLikeDatasets:()=>datasets,isValidCatalogYear:value=>Number.isInteger(Number(value))&&Number(value)>=1900&&Number(value)<=2100,
        createOption:(value,label)=>({value:String(value),label}),refreshCustomSelect(){},markScoreFiltersDirty(){},currentRoute:'overview',scoreRequestId:0,
    });
    c.renderScoreYearOptions();
    assert.equal(select.value,'2026');
    assert.deepEqual(select.options.map(o=>o.value),['','2026','2024']);
    select.value='';
    c.renderScoreYearOptions();
    assert.equal(select.value,'');
});

function scoreSearchContext(fetch){
    const elements={};
    const element=id=>elements[id]??={value:'',dataset:{},innerHTML:''};
    const history=[];
    const seen=[];
    const c=load(['searchScoreReferences'],{
        document:{getElementById:element},getValue:id=>element(id).value,fetch,URLSearchParams,
        SCORE_REFERENCES_API_URL:'/api/v1/agent/score-references',authToken:'',scoreRequestId:0,
        setButtonLoading(){},createLoadingPanel:String,readResponseError:async()=>'',
        renderScoreResults:data=>seen.push(data.year),addAnalysisHistoryRecord:r=>history.push(r),
        createErrorState:()=>'',showToast(){},console,
    });
    return {c,element,history,seen};
}

test('score query sends the selected recruitment year and stores that same year in history',async()=>{
    let requested;
    const {c,element:e,history}=scoreSearchContext(async url=>{
        requested=url;return {ok:true,json:async()=>({year:2026,returned:1})};
    });
    e('scoreYear').value='2026';
    await c.searchScoreReferences();
    assert.equal(new URL(requested,'http://test').searchParams.get('year'),'2026');
    assert.equal(Number(history[0].year),2026);
    e('scoreYear').value='';
    await c.searchScoreReferences();
    assert.equal(new URL(requested,'http://test').searchParams.has('year'),false);
    assert.equal(history[1].year,null);
});

test('a late score response for 2025 cannot replace the newer 2026 query',async()=>{
    const resolvers=[];
    const {c,element:e,seen}=scoreSearchContext(()=>new Promise(resolve=>resolvers.push(resolve)));
    e('scoreYear').value='2025';const old=c.searchScoreReferences();
    e('scoreYear').value='2026';const latest=c.searchScoreReferences();
    resolvers[1]({ok:true,json:async()=>({year:2026,returned:1})});await latest;
    resolvers[0]({ok:true,json:async()=>({year:2025,returned:1})});await old;
    assert.deepEqual(seen,[2026]);
});

test('filename import prefers recruitment year when written exam calendar year appears first',()=>{
    const c=load(['detectImportYear'],{});
    for(const name of ['2025年11月笔试_2026年度国考面试人员名单.xlsx','2025笔试_2026国考分数线.xlsx','笔试2025_招考年度2026_国考成绩.xlsx'])
        assert.equal(c.detectImportYear(name),'2026',name);
    for(const name of ['2025年11月笔试成绩.xlsx','笔试年份2025_成绩表.csv'])
        assert.equal(c.detectImportYear(name),'',name);
});


test('current city wins over saved profile even when asking by profile', () => {
    const profile={province:'广东',city:'',exam_type:'国考',education:'本科',major:'软件工程',identity:'应届生'};
    const filters={...profile,city:'汕头'};
    const c=load(['buildAnalystPayload'],{
        userProfile:profile,getCurrentAnalysisFilters:()=>filters,
        inferAnalysisContextPreference:()=> 'profile', inferQuestionLocationPreference:()=>({}),
        isPolicyQuestion:()=>false,isJobRecommendationQuestion:()=>true,getValue:()=>'',
        inferResultLimit:()=>1,activeSessionId:null,currentUser:null,authToken:'',
        currentJobContext:null,lastRecommendationJobs:[],currentAnalystSelectionPlan:{},buildCurrentJobPayload:v=>v,
    });
    assert.equal(c.buildAnalystPayload('按我的画像推荐一个岗位').city,'汕头');
    c.userProfile={...profile,city:'汕头'};
    filters.city='湛江';
    assert.equal(c.buildAnalystPayload('按我的画像推荐一个岗位').city,'湛江');
    filters.city='不限';
    assert.equal(c.buildAnalystPayload('按我的画像推荐一个岗位').city,'不限');
    assert.equal(c.userProfile.city,'汕头');
    filters.city='揭阳';
    c.inferQuestionLocationPreference=()=>({city:'汕头',province:'广东'});
    assert.equal(c.buildAnalystPayload('推荐汕头岗位').city,'汕头');
});

test('a late province option response cannot erase a newly typed profile city', async () => {
    const city={value:''}; const province={value:'广东'}; let resolve; const calls=[];
    const c=load(['handleProfileProvinceChange'],{
        PROFILE_UNSET_OPTION:'未填写',getValue:id=>id==='profileProvince'?province.value:city.value,
        fetchOptions:()=>new Promise(done=>{resolve=done}),normalizeArray:v=>v||[],
        populateSelect:(id,values,selected)=>{calls.push(selected);city.value=selected;},
        showToast(){},getErrorText:String,
    });
    const pending=c.handleProfileProvinceChange({target:{value:'广东'}});
    city.value='汕头'; resolve({cities:['广州','汕头']}); await pending;
    assert.equal(city.value,'汕头');
});


test('late analyst city options preserve a newer city choice and ignore an older province', async () => {
    const fields={analystRegion:'广东',analystCity:'不限'}; const resolvers={}; const calls=[];
    const c=load(['refreshCityOptions'],{
        getValue:id=>fields[id],fetchOptions:name=>new Promise(done=>{resolvers[name]=done}),
        normalizeArray:v=>v||[],populateSelect:(id,values,selected)=>{calls.push({id,values,selected});fields[id]=selected;},
        showToast(){},getErrorText:String,
    });
    const first=c.refreshCityOptions('analystCity','广东','不限');
    fields.analystCity='汕头';resolvers['广东']({cities:['汕头']});await first;
    assert.equal(fields.analystCity,'汕头');
    const stale=c.refreshCityOptions('analystCity','广东','不限');
    fields.analystRegion='广西';fields.analystCity='南宁';
    const latest=c.refreshCityOptions('analystCity','广西','不限');
    resolvers['广西']({cities:['南宁']});await latest;
    const count=calls.length;resolvers['广东']({cities:['汕头']});await stale;
    assert.equal(calls.length,count);
});


test('current controls use profile only for empty values, preserving explicit unlimited',()=>{
    const fields={analystCity:'揭阳',analystMajor:'不限'};
    const c=load(['getCurrentAnalysisFilters'],{getValue:id=>fields[id]||'',
        userProfile:{province:'广东',city:'汕头',education:'本科',major:'软件工程',identity:'应届生',exam_type:'国考'}});
    const filters=c.getCurrentAnalysisFilters();
    assert.equal(filters.city,'揭阳');assert.equal(filters.province,'广东');
    assert.equal(filters.major,'不限');assert.equal(filters.education,'本科');
});

test('refreshing options preserves temporary analyst conditions',()=>{
    const fields={analystRegion:'广西',analystCity:'南宁',analystEducation:'硕士',analystIdentity:'普通人员'};
    const c=load(['populateBaseOptions'],{getValue:id=>fields[id]||'',
        userProfile:{province:'广东',city:'汕头',education:'本科',identity:'应届生'},
        ALL_PROVINCES:['广东','广西'],PROFILE_UNSET_OPTION:'未填写',
        uniqueValues:v=>[...new Set(v)],normalizeArray:v=>v||[],
        populateSelect:(id,values,selected)=>{fields[id]=selected;},
        document:{getElementById:()=>null}});
    c.populateBaseOptions({cities:['汕头'],educations:['本科','硕士'],identities:['普通人员']});
    assert.equal(fields.analystRegion,'广西');assert.equal(fields.analystCity,'南宁');
    assert.equal(fields.analystEducation,'硕士');assert.equal(fields.analystIdentity,'普通人员');
});

test('recognized conditions display submitted city without a city in question',()=>{
    const elements={};const c=load(['renderRecognizedConditions'],{
        document:{getElementById:id=>elements[id]??=( {textContent:'',hidden:true})}});
    c.renderRecognizedConditions({city:'揭阳',region:'广东',location_preference:{},result_limit:1});
    assert.match(elements.analystRecognizedConditions.textContent,/揭阳/);
    assert.equal(elements.analystRecognizedConditions.hidden,false);
});


test('category existence questions submit as job recommendations',()=>{
    const c=load(['isJobRecommendationQuestion'],{});
    for(const question of ['有没有揭阳公安岗的','有无揭阳税务岗位','是否有海关岗位'])
        assert.equal(c.isJobRecommendationQuestion(question),true,question);
});


test('analysis provenance distinguishes discarded text from accepted model output',()=>{
    const c=load(['analysisProvenanceText'],{});
    const text=c.analysisProvenanceText({analysis_metadata:{output_source:'rules',
        report:{call_succeeded:true,accepted:false,reasons:['unverified_numeric_claim']},
        selection:{total_qualified_candidates:12,compared_count:0,model_used:false},skill:{applied:false}}});
    assert.match(text,/规则/);assert.doesNotMatch(text,/模型分析已采纳/);
    const used=c.analysisProvenanceText({analysis_metadata:{output_source:'model',report:{accepted:true},
        selection:{model_used:true,compared_count:8,total_qualified_candidates:20},skill:{applied_to_selection:true}}});
    assert.match(used,/公开方法/);assert.match(used,/20/);assert.match(used,/8/);
});

test('effective server conditions update untouched controls but not newer edits',()=>{
    const state={exam_type:'国考',province:'广东',city:'揭阳',education:'本科',major:'软件工程',identity:'应届生'};
    const calls=[];
    const c=load(['applyEffectiveAnalysisConditions'],{getCurrentAnalysisFilters:()=>state,
        setValue:(id,value)=>calls.push([id,value]),populateSelect:(id,values,value)=>calls.push([id,value]),
        document:{getElementById:()=>({tagName:'INPUT'})},renderAnalysisConditionSurfaces(){}});
    const payload={current_filters:{...state}};
    c.applyEffectiveAnalysisConditions({analysis_metadata:{effective_conditions:{...state,city:'深圳'}}},payload);
    assert.ok(calls.some(([id,value])=>id==='analystCity'&&value==='深圳'));
    calls.length=0;state.city='汕头';
    c.applyEffectiveAnalysisConditions({analysis_metadata:{effective_conditions:{...state,city:'深圳'}}},payload);
    assert.equal(calls.length,0);
});


test('current job payload preserves public extra qualifications through normalization', () => {
    const c = load(['normalizeJob', 'buildCurrentJobPayload'], {
        getDisplayPositionCode: job => job.position_code || '',
        firstPresent: (...values) => values.find(value => value !== undefined && value !== null),
        optionalNumber: value => value == null ? null : Number(value),
        sanitizeUserFacingText: value => String(value || ''),
        normalizePositionCode: value => String(value || ''),
        getJobDisplayTitle: job => job.position_name || '测试岗位',
        formatPublicJobFlag: value => String(value || ''),
        normalizeArray: value => Array.isArray(value) ? value : [],
        normalizeJobRiskFlags: () => [], normalizeRiskLevel: () => '',
    });
    const normalized = c.normalizeJob({position_code:'333333', province:'广东', year:2027,
        exam_type:'国考', eligibility_extra_requirements:'性别：女性；年龄要求：30周岁以下'});
    const context = c.buildCurrentJobPayload(normalized);
    assert.equal(context.eligibility_extra_requirements, '性别：女性；年龄要求：30周岁以下');
});
