export function taskInputs(request) {
  if (request?.method !== 'elicitation/create' || !['form', undefined].includes(request.params?.mode)) return null;
  const schema = request.params?.requestedSchema;
  if (schema?.type !== 'object' || !schema.properties || typeof schema.properties !== 'object' || Array.isArray(schema.properties)) return null;
  const fields = Object.entries(schema.properties);
  if (fields.length > 20 || fields.some(([, field]) => !field || typeof field !== 'object' || !['string', 'number', 'integer', 'boolean'].includes(field.type)
      || (field.enum !== undefined && (!Array.isArray(field.enum) || field.enum.some(value => !['string', 'number', 'boolean'].includes(typeof value)))))) return null;
  return fields.map(([name, field]) => ({ ...field, name, required: Array.isArray(schema.required) && schema.required.includes(name) }));
}

export function taskAnswer(fields, values) {
  const result = {};
  for (const field of fields) {
    const value = values[field.name];
    if (value === undefined || value === '') {
      if (field.required) throw new Error(`请填写 ${field.title || field.name}`);
      continue;
    }
    if (field.type === 'boolean') {
      if (!['true', 'false'].includes(value)) throw new Error('请选择明确的同意或不同意');
      result[field.name] = value === 'true';
    } else if (['number', 'integer'].includes(field.type)) {
      const number = Number(value);
      if (!Number.isFinite(number) || (field.type === 'integer' && !Number.isInteger(number))) throw new Error(`请检查 ${field.title || field.name} 的数值`);
      result[field.name] = number;
    } else result[field.name] = value;
  }
  return result;
}
