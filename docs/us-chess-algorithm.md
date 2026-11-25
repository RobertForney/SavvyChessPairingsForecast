# US Chess procedural forecasting

Reference: [US Chess rules, revised January 1, 2026](https://new.uschess.org/sites/default/files/media/documents/us-chess-rule-book-online-2026.pdf), chapters 27–29.

The engine processes descending score brackets. It constructs natural split-half pairings, repairs prohibited encounters, then improves colors without introducing extra score-bracket crossings. Odd-player selection starts with the lowest-rated rated player and backtracks when lower groups cannot finish. Repeated-float penalties do not override US priorities.

Color entitlement compares imbalance, chronological differences including unplayed gaps, then rank. Optional switches use the smaller equivalent rating change for transpositions, the actual boundary change for interchanges, and the 80/200-point limits. A qualifying 80-point transposition precedes an equally effective interchange. Limits compare original and final pairings, including linked odd-player boards.

Half-bye recipients remain eligible under the conditional 28L4 exception. Reentry histories follow 28S; reviewed entry selection determines the carried score. Strict final-round series exceptions require explicit prize contenders and a failed ordinary search.

Published numerical examples test direct swaps and intervening-board shifts. Pure permutation cycles use conservative final-displacement limits; larger mixed interchange cycles remain unsupported. Bounded search, director choices, and merged schedules still limit exact reproduction. Diagnostics identify truncation, exceptions, and the selected procedure.
