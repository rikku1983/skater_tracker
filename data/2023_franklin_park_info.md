# Step 1: get division and distance structure from distance classification section
- Lamborghini: 11 skaters
    - 1000
    - 500
    - 1500
- Bugatti: 12 skaters
    - 777
    - 500
    - 1000
- Maserati: 11 skaters
    - 500
    - 333
    - 777
- Porsche: 6 skaters
    - 500
    - 333
    - 777
- Alfa Romeo: 5 skaters
    - 444
    - 444（#2）
    - 333
    - 333 (#2)
    - 500
- Aston Martin: 4 skaters
    - 444
    - 444 (#2)
    - 333
    - 333 (#2)
    - 500
- Hot Wheels: 5 skaters
    - 222
    - 222 (#2)
    - 111
    - 111 (#2)
    - 333
- Yugo: 5 skaters
    - 777
    - 777 (#2)
    - 500
    - 500 (#2)
    - 1000

Note that normally, there would be at least 3 distances for 1 division (some event might have 4 distances). For most of the distances, there would be 2 rounds, heat and final (some events might have 3 or more rounds). Using heat to put skaters into A or B finals. This is mainly because you cannot have too many skaters (for 500m, you cannot have more than 5) to compete at the same time. Sometimes for the last distance would be a 'Super final' which means there is only 1 round. So each skater would skate 5 times in total for this event. But for divisions with 5 or less skaters, because you don't need to put them into different groups using a heat run, they can directly do final race for all the distances. But people want to maker sure all skaters have similar number of races, so for these groups with less than 6 skaters, they do twice of first two distances. You can see the last 4 groups above, they do the first 2 distances twice. Because there is no heat run, so all of them are final, so each of them get a classification table.

With above information in mind, then you look into the all results tables. They should be arranged from first division to last division for 5 rounds. It will starts with Lamborghini 1000m event (101, 102, and 103, 3 heats) ==>  Bugatti 777m event (201, 202, 203) ==> Maserati 500m (301, 302, 303) ==> Porsche 500m (401 402) ==> Alfa Romeo 444m (501) ==> Aston Martin 444m (601) and so on until Yugo 777m (801). This concludes the first round. Then it will start the second round, Lamborghini 1000m event (901, 902, and 903, 3 finals) ==> Bugatti 777m event (1001, 1002, 1003, 3 finals) and so on. There should be 8 division times 5 rounds, 40 events. Indeed, the last event is 4001. 

So this way, you can find out the event types (division and distances) from the result tables. Also you can identify duplicated events with same event numbers. You can also compare the skaters when you are imputing the division and distances to confirm that is correct.

Above all, I recommend before parsing this type of format, construct the above events order first would be very helpful.