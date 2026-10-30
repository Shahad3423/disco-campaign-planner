# Holdout: 100 real-brand briefs (blind to scoring)

```json
{
 "n": 100,
 "errors": 0,
 "gate_agreement_with_expected": 0.67,
 "segments": {
  "edge": {
   "n": 15,
   "gates": {
    "off_network": 8,
    "proceed": 7
   },
   "mean_confidence": 0.72,
   "mean_recommended": 1.0,
   "weak_fit_rate_when_planned": 0.0
  },
  "in_catalog": {
   "n": 55,
   "gates": {
    "proceed": 55
   },
   "mean_confidence": 0.872,
   "mean_recommended": 3.345,
   "weak_fit_rate_when_planned": 0.074
  },
  "out_of_catalog": {
   "n": 30,
   "gates": {
    "proceed": 30
   },
   "mean_confidence": 0.827,
   "mean_recommended": 2.033,
   "weak_fit_rate_when_planned": 0.435
  }
 },
 "confidence_by_clarity": {
  "clear": 0.847,
  "medium": 0.836,
  "vague": 0.74
 },
 "host_recommendation_share": {
  "Swiftcart": 0.27,
  "Daily Form": 0.24,
  "Hearthstone Goods": 0.24,
  "Northbed": 0.22,
  "Pantrygood": 0.22,
  "Movewell": 0.19,
  "Kitchenly": 0.19,
  "Cloudfoot": 0.18,
  "Heartfoot": 0.17,
  "Marlowe & Co.": 0.14,
  "Velvetline": 0.14,
  "Strandlab": 0.13,
  "Pop & Sip": 0.12,
  "Everbody": 0.11,
  "Pawline": 0.1,
  "Ruffco": 0.1,
  "Tailcrate": 0.1,
  "Linden Park": 0.1,
  "Stride & Stem": 0.1,
  "Studiogrid": 0.08
 },
 "hosts_never_recommended": [],
 "creatives": {
  "plans_with_creatives": 92,
  "rule_fail": 0,
  "review_fail": 62,
  "regulated_claims_in_copy": 0,
  "briefs_with_blocked_claims": 1
 },
 "wall_time_s": 52.0
}
```

## Gate mismatches vs the writer's rough expectation

| id | brand | expected | got | brief |
|---|---|---|---|---|
| h006 | Honest Paws | clarify | proceed | Hemp oil and chews for anxious or older dogs. Vet-recommended, 30-day guarantee. Not sure  |
| h023 | Olaplex | clarify | proceed | Bond repair for damaged hair. Everyone should be using it lol. Salon and retail. |
| h056 | Anker | off_network | proceed | Fast chargers, power banks, cables. Mostly $20-60 purchases. We want phone-and-laptop user |
| h057 | Casper | off_network | proceed | Mattresses and bedding, 100-night trial, free shipping and returns. Mattresses run $600-2, |
| h058 | Away | off_network | proceed | Carry-on and checked suitcases, $275-$450, lifetime warranty. Frequent flyers and gift-giv |
| h060 | Article | off_network | proceed | Mid-century modern furniture shipped direct. Sofas cost around $1,000-2,000 and delivery t |
| h063 | Brilliant | off_network | proceed | Interactive math, science and CS courses on mobile and web. Subscription is roughly $25/mo |
| h064 | MasterClass | off_network | proceed | Annual subscription (~$120-180) to video classes from famous experts. Big gifting spike in |
| h065 | Chime | off_network | proceed | Banking app with no monthly fees and early direct deposit. Mainly younger and mid-income A |
| h066 | Lemonade | off_network | proceed | Renters and pet insurance starting at about $5 a month, all by app. Looking for new renter |
| h067 | Dry Farm Wines | off_network | proceed | Low-sugar, lab-tested natural wines shipped monthly. $160-ish a box. Adults 21+ who care a |
| h068 | Charlotte's Web | off_network | proceed | Hemp-extract CBD gummies, oil and creams. $40-90 a bottle. Stress, sleep, exercise recover |
| h069 | Warby Parker | off_network | proceed | Prescription glasses starting at $95 including lenses, free home try-on of 5 frames. Anyon |
| h070 | Cotopaxi | off_network | proceed | Colorful outdoor gear - packs, jackets, duffels - from a certified B Corp. Lots of 'Del Di |
| h071 | WeatherTech | off_network | proceed | Custom-fit floor liners and cargo mats, made in USA. Sets are $150-250. Targeting people w |
| h072 | Hinge | off_network | proceed | Dating app 'designed to be deleted'. Free to download, premium tiers exist. Singles 22-38  |
| h073 | CuriosityStream | off_network | proceed | Documentary streaming from $4.99/month. Older, intellectually-curious audiences, science a |
| h077 | Bookshop.org | off_network | proceed | Online bookstore that supports independent bookstores. Typical basket $30. Book lovers who |
| h078 | Purple | off_network | proceed | Mattresses, pillows and seat cushions with a patented grid design. $1,000+ for a mattress. |
| h079 | Sonos | off_network | proceed | Wireless speakers and soundbars, $120-$900. Home audio fans, new home owners, tech-forward |
| h081 | Ring | off_network | proceed | Video doorbells and security cameras starting at $60. New homeowners, renters, package-the |
| h084 | Peloton | off_network | proceed | Connected bikes and treadmills ($1,500+) plus an app membership for $12-44 a month. Lookin |
| h085 | Kindle (Amazon) | off_network | proceed | E-readers, $100-$200. For avid readers, travelers, and as gifts. |
| h088 | Gorgias | clarify | off_network | Customer support helpdesk built for Shopify merchants. Our audience is literally ecommerce |
| h089 | Rippling | reject_low_signal | off_network | All-in-one HR and IT for companies. Want leads. |
| h090 | Mailchimp | clarify | off_network | Email marketing platform. Free plan, paid starting around $13. Small businesses and creato |
| h093 | Best Friends Animal Society | clarify | proceed | Non-profit working to end the killing of dogs and cats in shelters. We're looking for mont |
| h094 | charity: water | clarify | proceed | We fund clean water projects in developing countries. Looking to turn thank-you page visit |
| h095 | DraftKings | off_network | proceed | Sports betting and daily fantasy for adults 21+ in states where it's legal. Goal is first- |
| h096 | Palmetto State Armory | off_network | proceed | Firearms, ammo and accessories retail. Customers must be 18/21+ and ship to an FFL for gun |
| h097 | Hims & Hers | off_network | proceed | Online visits and prescriptions for things like hair loss and ED, plus skincare. Men 25-50 |
| h099 | Tom's of Maine | clarify | proceed | Natural toothpaste and deodorant, you know the stuff. Just show it to whoever. Cheap, like |
| h100 | Blendtec | clarify | proceed | blenders!! high powered, ppl love them. also commercial but mainly home. promo going on ti |
