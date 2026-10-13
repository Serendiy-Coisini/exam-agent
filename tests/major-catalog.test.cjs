const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function setup(apiFetch=async()=>({status:'missing',entries:[]})) {
    const elements={},ready=[];
    const element=id=>elements[id] ||= {value:'',innerHTML:'',textContent:'',disabled:false,tagName:'SELECT',listeners:{},addEventListener(type,handler){this.listeners[type]=handler;}};
    const c=vm.createContext({document:{getElementById:element,addEventListener(type,handler){if(type==='DOMContentLoaded') ready.push(handler);}},apiFetch,
        userProfile:{education:'本科'},refreshCustomSelect(){},normalizeArray:v=>Array.isArray(v)?v:[],
        escapeHtml:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
    vm.runInContext(fs.readFileSync('app/static/major-catalog.js','utf8'),c);
    return {c,element,initialize:()=>ready.forEach(handler=>handler())};
}
const entries=[{code:'990101',name:'虚构甲专业',discipline_code:'99',discipline_name:'虚构门类',category_code:'9901',category_name:'虚构专业类',source_page:1},
    {code:'990102T',name:'虚构乙专业',discipline_code:'99',discipline_name:'虚构门类',category_code:'9901',category_name:'虚构专业类',source_page:1}];

test('exact catalog name fills hierarchy and code; a similar name never does',async()=>{
    const {c,element:e}=setup(async()=>({status:'ready',entries,year:2026,source_file:'fiction.pdf'}));
    e('profileMajorEducation').value='本科';
    await c.loadMajorCatalog();
    e('profileMajor').value='虚构甲专业';
    c.syncMajorFromName('profile');
    assert.equal(e('profileMajorCode').value,'990101');
    assert.equal(e('profileMajorDiscipline').value,'虚构门类');
    assert.equal(e('profileMajorCategory').value,'虚构专业类');
    e('profileMajor').value='虚构甲专业技术';
    c.syncMajorFromName('profile');
    assert.equal(e('profileMajorCode').value,'');
    assert.match(e('profileMajorCatalogStatus').textContent,/未.*找到|未.*收录/);
});

test('catalog code preserves suffix and undergraduate hierarchy is not applied to graduate profile',async()=>{
    const {c,element:e}=setup(async()=>({status:'ready',entries,year:2026,source_file:'fiction.pdf'}));
    e('eligibilityMajorEducation').value='本科';
    await c.loadMajorCatalog();
    e('eligibilityMajorCode').value='990102T';
    c.syncMajorFromCode('eligibility');
    assert.equal(e('eligibilityMajor').value,'虚构乙专业');
    e('eligibilityMajorEducation').value='研究生';
    e('eligibilityMajorCode').value='';
    e('eligibilityMajor').value='虚构乙专业';
    c.syncMajorFromName('eligibility');
    assert.equal(e('eligibilityMajorCode').value,'');
});

test('a late catalog load resolves the current name and never restores an older draft',async()=>{
    let resolve;
    const {c,element:e}=setup(()=>new Promise(r=>resolve=r));
    e('profileMajorEducation').value='本科';
    e('profileMajor').value='虚构甲专业';
    const loading=c.loadMajorCatalog();
    e('profileMajor').value='虚构乙专业';
    resolve({status:'ready',entries,year:2026,source_file:'fiction.pdf'});
    await loading;
    assert.equal(e('profileMajor').value,'虚构乙专业');
    assert.equal(e('profileMajorCode').value,'990102T');
});

test('catalog retries after authentication and authentication hooks await it',async()=>{
    let authenticated=false;
    const {c,element:e}=setup(async()=>{
        if(!authenticated) throw new Error('401');
        return {status:'ready',entries,year:2026};
    });
    e('profileMajorEducation').value='本科';
    e('profileMajor').value='虚构甲专业';
    await c.loadMajorCatalog();
    assert.equal(e('profileMajorCode').value,'');
    authenticated=true;
    await c.loadMajorCatalog();
    assert.equal(e('profileMajorCode').value,'990101');
    const app=fs.readFileSync('app/static/app.js','utf8');
    const login=app.slice(app.indexOf('async function handleDevLoginSubmit'),app.indexOf('function applyServerProfile'));
    const restore=app.slice(app.indexOf('async function restoreAuthSession'),app.indexOf('function renderAccountState'));
    assert.match(login,/loadMajorCatalog\(true\)/);
    assert.match(restore,/loadMajorCatalog\(true\)/);
});

test('catalog resolves an exact name immediately on input without destroying a partial draft',async()=>{
    const {c,element:e,initialize}=setup(async()=>({status:'ready',entries}));
    initialize();
    await c.loadMajorCatalog();
    e('profileMajorEducation').value='本科';
    e('profileMajorDiscipline').value='虚构门类';
    e('profileMajor').value='虚构';
    e('profileMajor').listeners.input();
    assert.equal(e('profileMajorDiscipline').value,'虚构门类');
    assert.equal(e('profileMajor').value,'虚构');
    e('profileMajor').value='虚构甲专业';
    e('profileMajor').listeners.input();
    assert.equal(e('profileMajorCode').value,'990101');
    assert.equal(e('profileMajorCategory').value,'虚构专业类');
});
