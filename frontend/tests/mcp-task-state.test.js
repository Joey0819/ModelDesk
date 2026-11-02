import test from 'node:test';
import assert from 'node:assert/strict';
import { taskAnswer, taskInputs } from '../src/mcp-task-state.js';

test('MCP answers require explicit choices and preserve their types', () => {
  const fields=taskInputs({method:'elicitation/create',params:{mode:'form',requestedSchema:{type:'object',properties:{approved:{type:'boolean'},copies:{type:'integer'},name:{type:'string'}},required:['approved','copies']}}});
  assert.throws(()=>taskAnswer(fields,{}),/请填写/);
  assert.deepEqual(taskAnswer(fields,{approved:'false',copies:'2',name:'小册子'}),{approved:false,copies:2,name:'小册子'});
  assert.throws(()=>taskAnswer(fields,{approved:'true',copies:'1.5'}),/数值/);
});
test('URL, sampling and nested requests never become executable UI actions', () => {
  assert.equal(taskInputs({method:'sampling/createMessage',params:{}}),null);
  assert.equal(taskInputs({method:'elicitation/create',params:{mode:'url',url:'https://example.com'}}),null);
  assert.equal(taskInputs({method:'elicitation/create',params:{requestedSchema:{type:'object',properties:{nested:{type:'object'}}}}}),null);
  for (const properties of ['malformed', {broken:null}, {broken:{type:'string',enum:{}}}]) {
    assert.equal(taskInputs({method:'elicitation/create',params:{requestedSchema:{type:'object',properties}}}),null);
  }
});
