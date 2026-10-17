// The Excel library is loaded only after XLSX is selected.
export async function workbookBytes(matrix) {
  const XLSX = await import('./vendor/sheetjs-0.20.3.mjs');
  const sheet = XLSX.utils.aoa_to_sheet(matrix, {dense:true});
  const ojfColumn = matrix[0].indexOf('OJF');
  if (ojfColumn >= 0) {
    for (const row of sheet['!data'].slice(1)) {
      if (row[ojfColumn]?.t === 'n') row[ojfColumn].z = '0.000';
    }
  }
  sheet['!autofilter'] = {ref:sheet['!ref']};
  sheet['!cols'] = matrix[0].map(header => ({wch:header === 'Journal title' ? 48 : Math.min(28, Math.max(14, header.length))}));
  const book = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(book, sheet, 'Journal selection');
  return XLSX.write(book, {type:'array', bookType:'xlsx', compression:true});
}
