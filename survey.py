"""Survey inputs, copied from the published tables (Germany, all days unless noted).

MiD 2023, "Tabellarische Grundauswertung" (infas_Tabellen_MiD2023_7555_Bund.pdf), tables named below.
ZVE 2022, Destatis statistical report 63911 and the detail tables on destatis.de, minutes per participant.
"""
# MiD W7: trip start time, % per bin, base without regular business trips; purpose "analog MiD 2008":
# a trip home carries the purpose of the trip before it, so each row mixes outbound and return trips
BINS = [(5, 8), (8, 10), (10, 13), (13, 16), (16, 19), (19, 22), (22, 29)]    # hours; "nachts" 22-5
W7 = {
    "work":    [31, 11, 9, 18, 21, 6, 3],
    "edu":     [37, 11, 10, 30, 9, 1, 0],
    "shop":    [3, 17, 31, 21, 21, 5, 1],
    "errand":  [3, 13, 28, 24, 22, 7, 1],
    "leisure": [3, 9, 19, 24, 28, 13, 4],
    "escort":  [11, 14, 15, 28, 24, 6, 1],
}
W9_MEAN_MIN = 30.2                                 # MiD W9: mean trip duration, all trips

# Rows by day of the survey ("Stichtag") vs all days. MiD has no cross-tables of person type x day,
# so the per-type values below are scaled by these day / all-days ratios.
#   mobile:  % leaving home, P36.1          trips:   trips per person (all persons), P39.1
#   dist:    % of mobile persons with 1, 2, 3, 4, 5+ trips, P39.2
#   purpose: % of trips Arbeit .. Begleitung, W1      start: % of trips per W7 bin (all purposes), W7
#   work_min: ZVE 2022 paid work per participant (63911-10 Mon-Fri, 63911-11 Sat/Sun/holidays)
ALL_DAYS = dict(mobile=81, trips=2.9, dist=[9, 35, 16, 19, 22], purpose=[13, 17, 6, 14, 11, 30, 8])
DAYS = {
    "weekday":  dict(label="Tuesday", mobile=84, trips=3.2, dist=[7, 34, 15, 20, 23],
                     purpose=[16, 19, 8, 13, 11, 24, 8], start=[14, 12, 17, 23, 24, 7, 2], work_min=7 * 60 + 54),
    "saturday": dict(label="Saturday", mobile=79, trips=2.6, dist=[13, 33, 17, 17, 21],
                     purpose=[4, 9, 1, 25, 13, 42, 6], start=[4, 13, 26, 23, 20, 9, 3], work_min=5 * 60 + 32),
    "sunday":   dict(label="Sunday", mobile=71, trips=1.9, dist=[19, 39, 19, 14, 10],
                     purpose=[3, 6, 1, 5, 12, 64, 8], start=[3, 11, 24, 28, 22, 8, 2], work_min=5 * 60 + 32),
}

# Person types (MiD "Haupttätigkeit"); children under 6 ("Kind") are left out.
#   n:       weighted persons in MiD, P36.1 (sets the shares)
#   mobile:  % leaving home on the day, P36.1
#   trips:   % of mobile persons with 1, 2, 3, 4, 5+ trips, P39.2; mean in "mean"
#   purpose: % of trips Arbeit, Dienst, Ausbildung, Einkauf, Erledigung, Freizeit, Begleitung, W1
TYPES = {
    "full-time worker": dict(n=147228 + 5355, mobile=87, trips=[9, 32, 14, 19, 26], mean=4.1,
                             purpose=[21, 32, 1, 11, 9, 23, 4], anchor="work"),
    "part-time worker": dict(n=60989, mobile=88, trips=[9, 29, 15, 19, 29], mean=4.1,
                             purpose=[19, 20, 1, 14, 10, 26, 10], anchor="work"),
    "pupil":            dict(n=46088, mobile=82, trips=[4, 43, 14, 24, 15], mean=3.2,
                             purpose=[1, 0, 38, 5, 3, 41, 10], anchor="school"),
    "student":          dict(n=9464, mobile=83, trips=[9, 32, 18, 20, 21], mean=3.3,
                             purpose=[10, 2, 15, 16, 12, 42, 3], anchor="uni"),
    "homemaker":        dict(n=8239, mobile=72, trips=[15, 34, 15, 17, 19], mean=3.1,
                             purpose=[1, 0, 3, 29, 17, 33, 16], anchor=None),
    "retiree":          dict(n=92013, mobile=71, trips=[14, 36, 19, 16, 15], mean=3.0,
                             purpose=[2, 1, 0, 28, 24, 40, 4], anchor=None),
    "other non-working": dict(n=25850, mobile=72, trips=[16, 35, 16, 15, 18], mean=3.0,
                              purpose=[6, 1, 2, 23, 19, 39, 9], anchor=None),
}

# ZVE 2022, minutes per participant and day (work: see DAYS)
ZVE_SCHOOL_MIN = 4 * 60 + 51      # detail table Qualifikation/Bildung: "Unterricht in der Schule"
ZVE_UNI_MIN = 4 * 60 + 1          # same table: "Studium/Hochschule"
ZVE_SHOP_MIN = 64                 # Haushaltsführung: "Einkaufen und Inanspruchnahme von Fremdleistungen"
# out-of-home leisure: social contacts 1:15 (49.3%), entertainment and culture 3:00 (11.4%),
# sport 1:39 (33.2%), weighted by participation
ZVE_LEISURE_MIN = (75 * 49.3 + 180 * 11.4 + 99 * 33.2) / (49.3 + 11.4 + 33.2)
