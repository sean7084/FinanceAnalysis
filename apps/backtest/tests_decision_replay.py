"""The rollback wrapper must actually discard writes.

The pre-registration requires the decision-day arm to run inside a rolled-back transaction,
so the measurement cannot leave BacktestRun rows or trade decisions behind. This pins the
mechanism itself on a trivial write, which is testable without replaying a session.
"""
from django.test import TestCase

from apps.backtest.decision_replay import run_in_rolled_back_transaction
from apps.markets.models import Market


class RunInRolledBackTransactionTests(TestCase):
    def test_writes_inside_the_operation_are_discarded(self):
        before = Market.objects.count()

        def operation():
            Market.objects.create(code='ROLLBACK', name='Discarded')
            return 'kept'

        result = run_in_rolled_back_transaction(operation)
        self.assertEqual(result, 'kept', 'the return value must survive the rollback')
        self.assertEqual(Market.objects.count(), before)
        self.assertFalse(Market.objects.filter(code='ROLLBACK').exists())

    def test_an_exception_propagates_and_still_rolls_back(self):
        before = Market.objects.count()

        def boom():
            Market.objects.create(code='BOOM', name='Discarded')
            raise ValueError('engine failed')

        with self.assertRaises(ValueError):
            run_in_rolled_back_transaction(boom)
        self.assertEqual(Market.objects.count(), before)
        self.assertFalse(Market.objects.filter(code='BOOM').exists())

    def test_a_preexisting_row_survives(self):
        """The rollback must only discard what the operation wrote."""
        keep = Market.objects.create(code='KEEP', name='Survivor')
        run_in_rolled_back_transaction(lambda: Market.objects.create(code='GONE', name='X'))
        self.assertTrue(Market.objects.filter(pk=keep.pk).exists())
        self.assertFalse(Market.objects.filter(code='GONE').exists())