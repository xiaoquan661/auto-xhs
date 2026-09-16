const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
function days(actualB = null) {
 const context = vm.createContext({window: {}, document: {querySelector: () => null}, Date, Intl});
 vm.runInContext(fs.readFileSync('webui/employee-panel.js', 'utf8'), context);
 const accounts = ['a','b'].map(account_slot => ({machine_id:'pc',account_slot}));
 const state = {accounts, machines:[], results:[], tasks:[], goals:[], dailyPlans:[
  {plan_date:'2026-09-14',accounts:accounts.map(a=>({...a,participating:true,metrics:{browse:5}}))},
  {plan_date:'2026-09-15',accounts:accounts.map(a=>({...a,participating:true,metrics:{browse:5}}))}],
  employeeSummary:{daily_metrics:accounts.map((a,i)=>({...a,date:'2026-09-15',metrics:{browse:{actual:i?actualB:10}}}))}};
 return context.window.AutoXhsEmployeePanel({state,localDateValue:()=> '2026-09-15',
 workerDayMetrics:()=>[],resultDate:r=>r.date,workerTaskDate:r=>r.date}).operationsDays();
}
test('one row per date combines all accounts and preserves missing receipts',()=>{
 const rows=days();assert.equal(rows.length,2);assert.equal(rows[0].date,'2026-09-15');
 assert.equal(rows[0].metrics[0].target,10);assert.equal(rows[0].metrics[0].actual,10);
 assert.equal(rows[0].metrics[0].incomplete,true);assert.equal(rows[0].status,'等待回执');
 assert.equal(rows[1].metrics[0].actual,null);assert.equal(rows[0].metrics.length,6);
});
test('one account surplus cannot hide another account shortfall',()=>{
 assert.equal(days(0)[0].status,'未完成');assert.equal(days(5)[0].status,'顺利完成');
});
