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
