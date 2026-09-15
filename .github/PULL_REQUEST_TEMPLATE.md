# Pull Request Template

## Description
Explain the purpose and scope of changes made in this pull request.

## Type of Change
- [ ] Bugfix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update
- [ ] Refactoring (no behavior changes)
- [ ] Tests added
- [ ] Performance improvement

## Affected Components
List files or apps modified:
- `apps/markets/views.py`
- `frontend/src/pages/DashboardPage.tsx`

## How to Test
Detailed instructions for verifying the changes:
1. Apply migration (if any): `python manage.py migrate`
2. Run backend checks: `python manage.py check`
3. Run test suite: `python manage.py test --noinput`
4. Run frontend tests: `cd frontend && npm test`
5. Verify the change in browser at: `http://localhost:5173/...`

## Screenshots (UI Changes)
Attach GIF/screenshot comparisons showing before vs after.

## Checklist
- [ ] Self-reviewed my code
- [ ] Added/updated tests accordingly
- [ ] Commented hard-to-understand sections
- [ ] Made corresponding documentation changes
- [ ] No unrecorded model changes (`python manage.py makemigrations --check` passes)
- [ ] Ran lint/format checks with no warnings
- [ ] Rebased onto/up-to-date with base branch
- [ ] Confirmed no regression in existing functionality

## Related Issues
Fixes #[issue_number]

## Notes
Any additional context or questions for reviewers.
