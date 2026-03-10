# Python Curriculum Map (Classes 3 - 10)

Designing a curriculum for a 10 or 11-year-old (5th standard) is all about keeping things visual, relatable, and interactive. Since they have an attention span that works best in short bursts, the trick to a 1-hour class is to avoid 60 minutes of straight lecturing.

## ⏱️ 1-Hour Class Structure

Use this structure for **every** session:
- **10 mins: Warm-up!** (Recap last class, maybe a quick pop-quiz question for points).
- **15 mins: The New Concept.** (Introduce a new tool/syntax briefly).
- **15 mins: "We Do It."** (Code an example together, side-by-side).
- **15 mins: "You Do It!" (Challenge Mode).** (Give them a fun problem to solve on their own while you guide them).
- **5 mins: Wrap up & Show Off.** (Celebrate what they built).

---

## 🗺️ The Curriculum Map

### Class 3: Talking to the Computer (User Input)
- **Goal:** Teach the `input()` function and joining strings together (concatenation).
- **Why it's fun:** The computer finally talks *back* to them!
- **The Lesson Example:** Build a simple Chatbot. Have the computer ask: `name = input("What is your name? ")` and reply `print("Hello " + name + "!")`.
- **The Coding Challenge:** **"Mad Libs Story Generator"**.
  - *Problem:* Ask the student for an animal, a color, and a superpower. Then, `print` a silly story combining those variables. *(e.g., "One day, a blue tiger was walking down the street using its super-speed!")*

### Class 4: Math, Numbers, & "Magic" (Typecasting)
- **Goal:** Learn about integers (`int`), floats, and basic math (`+`, `-`, `*`, `/`). Learning how to convert string input into math numbers.
- **Why it's fun:** Computers are incredibly fast calculators. We can use them to figure out silly, huge numbers.
- **The Lesson Example:** The "Age in Days" Magic Trick. Ask for their age, multiply it by `365`, and tell them exactly how many days old they are.
- **The Coding Challenge:** **"The Minecraft / Roblox Currency Converter"**.
  - *Problem:* If 1 Dollar = 80 Robux (or PokeCoins), ask them how many dollars they have for their allowance, and calculate exactly how many Robux they can buy.

### Class 5: Conditional Logic (Part 1 - The Bouncer!)
- **Goal:** Introduce `if`, `else`, `==`, `>`, and `<`. Making decisions in code.
- **Why it's fun:** The program will react differently based on what they type!
- **The Lesson Example:** The "Secret Password" gatekeeper. Ask for a password. If it is `"Batman"`, print "Welcome to the Batcave!". Else, print "Intruder Alert!".
- **The Coding Challenge:** **"The Rollercoaster Height Checker"**.
  - *Problem:* Ask for their height in centimeters. If they are taller than 120cm, print "You can ride!", else print "Sorry, go eat more veggies!"

### Class 6: Conditional Logic (Part 2 - Choose Your Own Adventure)
- **Goal:** Introduce the `elif` (Else-if) statement to handle multiple choices.
- **Why it's fun:** They get to build their very own text-based video game!
- **The Lesson Example:** Give them a scenario: *You are facing a dragon.* Option 1: Fight. Option 2: Run. Option 3: Dance. Use `if`, `elif`, and `else` to print different outcomes based on their choice.
- **The Coding Challenge:** **"Expand the Adventure!"**
  - *Problem:* Make them create their own story with 3 choices. Ask them to try and make one hidden choice that results in finding a "Secret Treasure".

### Class 7: Introduction to Lists (Your Inventory)
- **Goal:** Learn what a `list[]` is, how to make one, and how to grab items from it using indexing like `[0]`.
- **Why it's fun:** You can tie this directly to video game inventories.
- **The Lesson Example:** Create a player inventory `backpack = ["Sword", "Shield", "Apple"]`. Show them how programmers count starting from `0`. Prove it by printing `backpack[0]`.
- **The Coding Challenge:** **"Top 3 Video Games"**.
  - *Problem:* Have them create a list of their 3 favorite video games. Then write a command to `.append()` a new game they want to buy to the end of the list.

### Class 8: Loops (The 'For' Loop)
- **Goal:** Teach the `for` loop and `range()`. The golden rule: "Don't Repeat Yourself!"
- **Why it's fun:** Show them how tedious it is to type `print("Hello")` 100 times, and then show them how to do it in 2 lines of code. It feels like super-speed.
- **The Lesson Example:** The "Robo-Announcer". Loop through the `backpack` list from last class and make the computer announce every item they own.
- **The Coding Challenge:** **"The Bart Simpson Chalkboard"**.
  - *Problem:* Make the computer print "I will not throw paper airplanes in class" 50 times using `for i in range(50)`.

### Class 9: Loops (The 'While' Loop)
- **Goal:** Teach `while` loops. Explain that this is how real video games stay open without immediately closing (the game loop!).
- **Why it's fun:** Accidental infinite loops are hilarious to kids.
- **The Lesson Example:** The "Annoying Little Brother". Run a loop that constantly asks `input("Are we there yet? ")` and only stops when the student types `"yes"`.
- **The Coding Challenge:** **"Guess the Secret Number"**.
  - *Problem:* Create a variable with a secret number (like 7). Use a while loop to keep asking the user to guess. If they don't guess 7, ask them again!

### Class 10: The Game Changer - Turtle Graphics! 🐢
- **Goal:** Introduce `import turtle`. This gives them actual *visual* output on the screen.
- **Why it's fun:** It's essentially coding a robot pen to draw shapes and art! This is usually when kids fall completely in love with Python.
- **The Lesson Example:** Show them `turtle.forward(100)` and `turtle.right(90)`. Put it in a `for` loop to draw a square!
- **The Coding Challenge:** **"Draw a Star!"**
  - *Problem:* Let them experiment with angles to try and draw a triangle, or change the pen color with `turtle.color("red")` to draw a cool star.

---

## 💡 Pro-Tips for Engaging a 5th Grader

1. **Speak their Language:** Don't use generic variable names like `x`, `y`, or `item`. Use variables like `vbucks`, `pikachu_health`, `minecraft_blocks`. Relate the code exactly to the games and YouTubers they like.
2. **Reframe Errors as "Bugs":** Kids hate getting the red error text because it feels like they failed. Call them "Bugs" and tell the kid that programmers are actually "Detectives". Give them a high-five when they successfully track down a missing parenthesis.
3. **Let Them Drive:** Even if they type slowly, let *them* be the one typing on the keyboard. Muscle memory is incredibly important, and they'll feel a larger sense of ownership over the program.
4. **Gamify the Lessons:** Create a simple chart or give them "XP Points" or imaginary "Badges" at the end of every class when they finish the daily "Challenge Mode".
