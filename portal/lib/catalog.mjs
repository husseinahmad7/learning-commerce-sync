export const courses = [
  { code: 'PY101', title: 'Python Foundations', description: 'Learn to model data, write functions, and test small programs.', price: '99.00' },
];
export function courseLink(code, origin = process.env.LMS_ORIGIN, path = process.env.LMS_PY101_PATH) {
  if (!courses.some(course => course.code === code) || !origin || !path?.startsWith('/') || path.startsWith('//') || path.includes('\\')) return null;
  try {
    const base = new URL(origin);
    if (base.username || base.password || !['http:', 'https:'].includes(base.protocol)) return null;
    if (base.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)) return null;
    const target = new URL(path, base.origin);
    return target.origin === base.origin ? target.href : null;
  } catch { return null; }
}
