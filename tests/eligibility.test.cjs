const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(apiFetch=async()=>({})) {
    const path='app/static/eligibility.js';
    assert.ok(fs.existsSync(path),'Full eligibility UI should exist');
    const elements={};
    const element=id=>elements[id] ||= {value:'',checked:false,disabled:false,hidden:false,textContent:'',innerHTML:'',addEventListener(){}};
    const c=vm.createContext({document:{getElementById:element,addEventListener(){}},apiFetch,
        getErrorText:String, getValue:id=>element(id).value, userProfile:{},
        escapeHtml:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
        formatNumber:String, normalizeArray:v=>Array.isArray(v)?v:[]});
    vm.runInContext(fs.readFileSync(path,'utf8'),c);
    return {c,element};
}

test('eligibility page and profile qualification fields are present',()=>{
    const html=fs.readFileSync('app/static/index.html','utf8');
    assert.ok(html.includes('data-route-target="eligibility"'));
    for(const id of ['eligibilityResults','eligibilityPrevious','eligibilityNext','profileGender','profileCet4','profileCet6','profileCertificates'])
        assert.ok(html.includes(`id="${id}"`),id);
});

test('qualification fields preserve zero scores and distinguish missing certificates',()=>{
    const {c,element:e}=setup();
    e('profileCet4').value='0';
    e('profileCertificates').value='法律职业资格A类；注册会计师';
    let profile=c.readEligibilityProfileFields('profile');
    assert.equal(profile.cet4_score,0);
    assert.equal(profile.cet6_score,null);
    assert.equal(profile.certificates_reviewed,null);
    assert.equal(profile.certificates.length,2);
    e('profileCertificatesKnown').checked=true;
    assert.equal(c.readEligibilityProfileFields('profile').certificates_reviewed,true);
});

test('late eligibility response cannot overwrite newer results',async()=>{
    const resolve=[];
    const {c}=setup(()=>new Promise(r=>resolve.push(r)));
    const seen=[];
    c.renderEligibilityResults=data=>seen.push(data.page);
    vm.runInContext('eligibilityQuery={profile:{},page_size:20}',c);
    const old=c.fetchEligibilityPage(1);
    const latest=c.fetchEligibilityPage(2);
    resolve[1]({page:2,items:[]}); await latest;
    resolve[0]({page:1,items:[]}); await old;
    assert.deepEqual(seen,[2]);
});

test('editing filters invalidates pagination and renders remark content safely',()=>{
    const {c,element:e}=setup();
    vm.runInContext('eligibilityQuery={profile:{}}',c);
    c.markEligibilityFiltersDirty();
    assert.equal(e('eligibilityNext').disabled,true);
    const html=c.renderEligibilityJob({job_name:'<img src=x onerror=evil()>',remark:'<script>evil()</script>',status:'pending',checks:[]});
    assert.ok(!html.includes('<img'));
    assert.ok(!html.includes('<script>'));
    assert.ok(html.includes('&lt;script&gt;'));
});


test('empty grassroots years are sent as unknown rather than an invalid number',async()=>{
    let sent;
    const {c,element:e}=setup(async(url,options)=>{sent=JSON.parse(options.body);return {page:1,total_pages:0,items:[],counts:{matched:0,pending:0,unmet:0}};});
    c.userProfile={grassroots_years:''};
    e('eligibilityStatus').value='candidate';
    c.handleEligibilitySubmit({preventDefault(){}});
    await Promise.resolve();
    assert.equal(sent.profile.grassroots_years,null);
});


test('extended qualifications round trip through reusable profile controls', () => {
    const {c,element:e}=setup();
    const values={hukou_province:'广东',hukou_city:'深圳',hukou_county:'南山区',grassroots_years:0,
        fresh_graduate_scope:'current_year',service_projects:['三支一扶','西部计划'],
        service_projects_reviewed:true,service_completed:false,service_assessment_passed:true,
        service_years:0,retired_soldier:false,retired_college_graduate:false,military_years:0,
        law_a_certificate:false,major_code:'990101',major_discipline:'虚构门类',major_category:'虚构专业类',major_education:'本科'};
    c.renderEligibilityProfileFields('profile',values);
    const profile=c.readEligibilityProfileFields('profile');
    for(const key of Object.keys(values)) assert.deepEqual(JSON.parse(JSON.stringify(profile[key])),values[key],key);
    c.renderEligibilityProfileFields('eligibility',values);
    const temporary=c.readEligibilityProfileFields('eligibility');
    for(const key of Object.keys(values)) assert.deepEqual(JSON.parse(JSON.stringify(temporary[key])),values[key],key);
});

test('My Profile and eligibility both expose extended qualification controls',()=>{
    const html=fs.readFileSync('app/static/index.html','utf8');
    for (const prefix of ['profile','eligibility']) {
        for (const field of ['HukouProvince','HukouCity','HukouCounty','Grassroots','FreshScope',
            'ServiceVillage','ServiceTeacher','ServiceThree','ServiceWest','ServiceSoldier',
            'ServiceCompleted','ServiceAssessment','ServiceYears','RetiredSoldier','RetiredCollege',
            'MilitaryYears','LawA','MajorCode','MajorDiscipline','MajorCategory','MajorEducation'])
            assert.ok(html.includes(`id="${prefix}${field}"`),prefix+field);
    }
    assert.ok(html.includes('id="eligibilityMajorRestriction"'));
});

test('temporary grassroots and law certificate override profile during filtering',async()=>{
    let sent;
    const {c,element:e}=setup(async(url,options)=>{sent=JSON.parse(options.body);return {page:1,total_pages:0,items:[],counts:{matched:0,pending:0,unmet:0}};});
    c.userProfile={grassroots_years:5,law_a_certificate:true};
    e('eligibilityGrassroots').value='0';
    e('eligibilityLawA').value='no';
    e('eligibilityMajorRestriction').value='category';
    c.handleEligibilitySubmit({preventDefault(){}});
    await Promise.resolve();
    assert.equal(sent.profile.grassroots_years,0);
    assert.equal(sent.profile.law_a_certificate,false);
    assert.equal(sent.major_restriction_level,'category');
});

test('the result summary identifies the rejected-only view',()=>{
    const {c,element:e}=setup();
    vm.runInContext("eligibilityQuery={status:'unmet'}",c);
    c.renderEligibilityResults({page:1,total_pages:1,total:10,scope_total:20,basic_candidate_count:8,items:[],counts:{matched:0,pending:10,unmet:10}});
    assert.match(e('eligibilitySummary').textContent,/正在查看.*明确不符合|正在查看.*被排除/);
    vm.runInContext("eligibilityQuery={status:'candidate'}",c);
    c.renderEligibilityResults({page:1,total_pages:1,total:10,scope_total:20,basic_candidate_count:8,items:[],counts:{matched:0,pending:10,unmet:10}});
    assert.doesNotMatch(e('eligibilitySummary').textContent,/正在查看.*被排除/);
});
