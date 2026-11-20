"""H2: stock fold plan wraps expanding_folds with contract-start training."""
from datetime import date

import pandas as pd
from django.test import SimpleTestCase

from apps.core.date_contract import MIN_TRAIN_SESSIONS, STOCK_TRAINING_START
from apps.prediction.walk_forward import N_FOLDS, stock_fold_plan, train_dates_for_fold


def _sessions(n=2500):
    return [d.date() for d in pd.bdate_range(STOCK_TRAINING_START, periods=n)]


class StockFoldPlanTests(SimpleTestCase):
    def test_seven_folds_validation_from_panel_start(self):
        sessions = _sessions()
        panel_start = sessions[300]
        folds = stock_fold_plan(panel_start, sessions[-1], sessions)
        self.assertEqual(len(folds), N_FOLDS)
        self.assertEqual(folds[0].valid_start, panel_start)
        # expanding: each fold trains from the contract start
        for fold in folds:
            self.assertEqual(fold.train_start, STOCK_TRAINING_START)
        # validation blocks are ordered and non-overlapping
        for earlier, later in zip(folds, folds[1:]):
            self.assertLess(earlier.valid_end, later.valid_start)

    def test_purge_applied_via_train_dates_for_fold(self):
        sessions = _sessions()
        folds = stock_fold_plan(sessions[300], sessions[-1], sessions)
        kept, dropped = train_dates_for_fold(folds[0], sessions, horizon_days=30)
        self.assertGreater(len(dropped), 0)  # purge removes the tail before validation
        self.assertTrue(all(d < folds[0].valid_start for d in kept))

    def test_panel_start_not_a_session_raises(self):
        sessions = _sessions()
        with self.assertRaises(ValueError):
            stock_fold_plan(date(2017, 1, 1), sessions[-1], sessions)  # weekend/holiday likely absent

    def test_too_few_training_sessions_raises(self):
        sessions = _sessions()
        with self.assertRaises(ValueError):
            stock_fold_plan(sessions[MIN_TRAIN_SESSIONS - 1], sessions[-1], sessions)