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
