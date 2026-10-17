#!/usr/bin/env python3
"""
pub-trivia-night — local-first self-hosted pub-trivia night generator.

Host pastes a brief (theme, difficulty mix, number of rounds, duration,
group vibe, venue) and gets back:
  * 8-round quiz structure with progressive difficulty
  * 8 questions per round (4 standard + 2 music/visual/audio + 2 picture-round)
  * Multiple-choice with 4 options (A/B/C/D), correct-answer key, host cue
  * Tiebreaker math problem (deterministic per session_id)
  * 50/50 + double-or-nothing mechanic per round (host-applied)
  * Host script with estimated minutes per round
  * Team-balance handicapping suggestion if N teams disclosed
  * 5-axis 0-100 night-feasibility score (balance / pace / ambiguity /
    accessibility / replayability)
  * Risk flags (overlap risk between rounds, runaway-leader risk, etc.)

Zero external API calls. SQLite session log + Markdown export.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

BASE = Path(__file__).parent
DB = BASE / "quiz.db"
app = Flask(__name__, static_folder="static", static_url_path="")


# ---------------------------------------------------------------------------
# Theme bank. Curated so the host gets a real catalog, not lorem-ipsum.
# ---------------------------------------------------------------------------

THEMES = {
    "general": {
        "label": "General Knowledge",
        "tags": ["misc"],
        "rounds": ["Picture Round", "Music Round", "Geography", "History",
                   "Science & Nature", "Film & TV", "Sports", "Food & Drink"],
        "description": "Broad trivia covering every category — the classic pub-quiz default.",
    },
    "geography": {
        "label": "World Geography",
        "tags": ["academic", "visual"],
        "rounds": ["Capital Cities", "Flags", "World Landmarks", "Borders & Borders",
                   "Maps & Mountains", "Rivers & Seas", "USA States", "Currencies"],
        "description": "Maps, capitals, flags, and country deep-cuts.",
    },
    "music": {
        "label": "Music Trivia",
        "tags": ["audio", "high-energy"],
        "rounds": ["80s Hits", "90s Hits", "00s Hits", "One-Hit Wonders",
                   "Album Covers", "Band Lineups", "Lyrics Trivia", "Instruments"],
        "description": "Audio clips, lyrics, and album-art identification.",
    },
    "movie-tv": {
        "label": "Film & Television",
        "tags": ["visual", "pop-culture"],
        "rounds": ["Blockbusters", "TV Sitcoms", "Movie Quotes", "Directors",
                   "Animated Films", "Award Winners", "Cult Classics", "Sci-Fi"],
        "description": "From blockbusters to cult favorites.",
    },
    "sports": {
        "label": "Sports",
        "tags": ["stats", "niche"],
        "rounds": ["Football (Soccer)", "NFL", "NBA", "Olympics",
                   "Tennis", "Boxing & MMA", "World Cup Records",
                   "Stadiums & Venues"],
        "description": "Goals, medals, rings, and records.",
    },
    "history": {
        "label": "History",
        "tags": ["academic"],
        "rounds": ["Ancient World", "Medieval", "20th Century", "Wars",
                   "Explorers", "American History", "World History", "Inventions"],
        "description": "Empires, revolutions, and dates worth remembering.",
    },
    "science": {
        "label": "Science & Nature",
        "tags": ["academic"],
        "rounds": ["Astronomy", "Biology", "Chemistry", "Physics",
                   "Animals", "Human Body", "Inventions", "Discoveries"],
        "description": "From quarks to quasars to the platypus.",
    },
    "food-drink": {
        "label": "Food & Drink",
        "tags": ["lifestyle"],
        "rounds": ["World Cuisines", "Cocktails", "Cheese & Wine", "Desserts",
                   "Fast Food", "Spices & Herbs", "Baking", "Coffee & Tea"],
        "description": "For the table that debates pizza folds.",
    },
    "literature": {
        "label": "Books & Literature",
        "tags": ["academic", "niche"],
        "rounds": ["Classic Novels", "Poetry", "Authors", "Famous First Lines",
                   "Children's Books", "Sci-Fi & Fantasy", "Pulitzer & Nobel",
                   "Book-to-Film"],
        "description": "Dostoevsky to Dr. Seuss, Dickens to detective noir.",
    },
    "1980s": {
        "label": "The 1980s",
        "tags": ["decade", "nostalgia"],
        "rounds": ["80s Movies", "80s Music", "80s TV", "80s Toys & Games",
                   "80s Tech", "80s Politics", "80s Fashion", "80s Sports"],
        "description": "Hammer pants, Rubik's cubes, and Reagan.",
    },
    "1990s": {
        "label": "The 1990s",
        "tags": ["decade", "nostalgia"],
        "rounds": ["90s Movies", "90s Music", "90s TV", "90s Tech",
                   "90s Toys", "90s Fashion", "90s Sports", "90s Politics"],
        "description": "Dial-up, Tamagotchis, and Friends.",
    },
    "office": {
        "label": "Workplace / Office Humor",
        "tags": ["light", "icebreaker"],
        "rounds": ["Office Supplies", "Meeting Bingo", "Cubicle History",
                   "Email Etiquette", "Office Movies", "Boss & Manager Trivia",
                   "Workplace Lingo", "Famous First Jobs"],
        "description": "Cubicles, Slack pings, and meeting-room lingo.",
    },
    "holiday": {
        "label": "Holidays & Celebrations",
        "tags": ["seasonal", "warm"],
        "rounds": ["Christmas", "Halloween", "Thanksgiving", "New Year",
                   "Easter", "Hanukkah", "Weddings", "Birthdays"],
        "description": "Festive trivia for festive tables.",
    },
    "tech": {
        "label": "Tech & Internet",
        "tags": ["modern", "niche"],
        "rounds": ["Internet History", "Computer Brands", "Gadgets",
                   "Video Games", "App & Software", "Tech Founders",
                   "Social Media", "Cyber Trivia"],
        "description": "From ENIAC to TikTok.",
    },
    "kids-birthday": {
        "label": "Kids' Birthday (8-12 yr)",
        "tags": ["family", "easy"],
        "rounds": ["Disney", "Animals", "Cartoon Characters", "Snacks",
                   "Roblox & Minecraft", "Superheroes", "Sports for Kids",
                   "School Stuff"],
        "description": "Family-friendly — keep it light and silly.",
    },
}


# ---------------------------------------------------------------------------
# Curated question bank. Each question keyed by theme + round slug. We seed
# per theme with a deep pool so we can draw 8/round deterministically.
# ---------------------------------------------------------------------------

QUESTION_BANK: dict[str, dict[str, list[dict]]] = {
    "general": {
        "Picture Round": [
            {"q": "Which famous landmark is in the background of this photo?",
             "choices": ["Eiffel Tower", "Big Ben", "Colosseum", "Statue of Liberty"],
             "answer": 0, "difficulty": "easy", "tag": "landmarks",
             "hint": "Wrought-iron lattice tower, Paris."},
            {"q": "What fruit is shown in the image?",
             "choices": ["Apple", "Peach", "Mango", "Pear"],
             "answer": 1, "difficulty": "easy", "tag": "produce"},
            {"q": "Identify the car brand shown.",
             "choices": ["Ford", "Tesla", "BMW", "Toyota"],
             "answer": 1, "difficulty": "easy", "tag": "automotive"},
            {"q": "What animal is this?",
             "choices": ["Llama", "Alpaca", "Camel", "Goat"],
             "answer": 1, "difficulty": "medium", "tag": "animals",
             "hint": "Smaller than its cousin, native to the Andes."},
            {"q": "Which logo belongs to the streaming service?",
             "choices": ["Spotify", "Netflix", "Hulu", "Disney+"],
             "answer": 1, "difficulty": "easy", "tag": "brands"},
            {"q": "What is the capital city whose flag is pictured?",
             "choices": ["Ottawa", "Washington DC", "Canberra", "Tokyo"],
             "answer": 2, "difficulty": "medium", "tag": "flags"},
            {"q": "Name the painting style shown.",
             "choices": ["Cubism", "Surrealism", "Impressionism", "Baroque"],
             "answer": 2, "difficulty": "medium", "tag": "art"},
            {"q": "What kind of building is pictured?",
             "choices": ["Museum", "Library", "Theater", "Bank"],
             "answer": 1, "difficulty": "medium", "tag": "architecture"},
        ],
        "Music Round": [
            {"q": "Which band released 'Bohemian Rhapsody' in 1975?",
             "choices": ["Led Zeppelin", "Queen", "The Beatles", "Pink Floyd"],
             "answer": 1, "difficulty": "easy", "tag": "classic-rock"},
            {"q": "Who sang 'Rolling in the Deep'?",
             "choices": ["Adele", "Sam Smith", "Ed Sheeran", "Taylor Swift"],
             "answer": 0, "difficulty": "easy", "tag": "2010s"},
            {"q": "Taylor Swift's 2022 album was called...",
             "choices": ["1989", "Folklore", "Midnights", "Evermore"],
             "answer": 2, "difficulty": "easy", "tag": "modern"},
            {"q": "Which instrument has 88 keys?",
             "choices": ["Organ", "Piano", "Harpsichord", "Accordion"],
             "answer": 1, "difficulty": "easy", "tag": "instruments"},
            {"q": "What year did The Beatles break up?",
             "choices": ["1968", "1970", "1972", "1974"],
             "answer": 1, "difficulty": "medium", "tag": "classic-rock"},
            {"q": "Beyoncé's 2016 visual album was called...",
             "choices": ["Lemonade", "Formation", "Beyoncé", "Renaissance"],
             "answer": 0, "difficulty": "medium", "tag": "modern-rnb"},
            {"q": "Which instrument family does the trumpet belong to?",
             "choices": ["Woodwind", "String", "Brass", "Percussion"],
             "answer": 2, "difficulty": "easy", "tag": "instruments"},
            {"q": "How many strings does a standard guitar have?",
             "choices": ["4", "5", "6", "7"],
             "answer": 2, "difficulty": "easy", "tag": "instruments"},
        ],
        "Geography": [
            {"q": "What is the capital of Australia?",
             "choices": ["Sydney", "Melbourne", "Canberra", "Perth"],
             "answer": 2, "difficulty": "easy", "tag": "capitals"},
            {"q": "Which is the longest river in the world (by most measures)?",
             "choices": ["Amazon", "Nile", "Yangtze", "Mississippi"],
             "answer": 1, "difficulty": "medium", "tag": "rivers"},
            {"q": "Mount Everest sits on the border of which two countries?",
             "choices": ["China / India", "Nepal / China", "Nepal / India", "Bhutan / Nepal"],
             "answer": 1, "difficulty": "medium", "tag": "mountains"},
            {"q": "Which country has the most time zones?",
             "choices": ["Russia", "USA", "China", "France"],
             "answer": 3, "difficulty": "hard", "tag": "trivia",
             "hint": "Includes overseas territories."},
            {"q": "What is the smallest country by area?",
             "choices": ["Monaco", "Vatican City", "San Marino", "Liechtenstein"],
             "answer": 1, "difficulty": "medium", "tag": "microstates"},
            {"q": "The Sahara Desert covers most of which region?",
             "choices": ["South America", "Central Asia", "North Africa", "Middle East"],
             "answer": 2, "difficulty": "easy", "tag": "deserts"},
            {"q": "Which African country has the largest population?",
             "choices": ["Egypt", "Ethiopia", "Nigeria", "South Africa"],
             "answer": 2, "difficulty": "medium", "tag": "africa"},
            {"q": "What is the capital of Iceland?",
             "choices": ["Oslo", "Reykjavík", "Helsinki", "Stockholm"],
             "answer": 1, "difficulty": "medium", "tag": "capitals"},
        ],
        "History": [
            {"q": "In which year did WWII end?",
             "choices": ["1943", "1944", "1945", "1946"],
             "answer": 2, "difficulty": "easy", "tag": "20c"},
            {"q": "Who was the first President of the United States?",
             "choices": ["Thomas Jefferson", "John Adams", "George Washington", "Benjamin Franklin"],
             "answer": 2, "difficulty": "easy", "tag": "usa"},
            {"q": "The Great Wall of China was built primarily to defend against whom?",
             "choices": ["Mongols", "Romans", "Japanese", "Vikings"],
             "answer": 0, "difficulty": "medium", "tag": "ancient"},
            {"q": "Which empire was ruled by Julius Caesar?",
             "choices": ["Greek", "Roman", "Ottoman", "Byzantine"],
             "answer": 1, "difficulty": "easy", "tag": "ancient"},
            {"q": "Who painted the Mona Lisa?",
             "choices": ["Michelangelo", "Raphael", "Da Vinci", "Donatello"],
             "answer": 2, "difficulty": "easy", "tag": "renaissance"},
            {"q": "The Cold War was primarily between the USA and which country?",
             "choices": ["China", "USSR", "Cuba", "East Germany"],
             "answer": 1, "difficulty": "easy", "tag": "20c"},
            {"q": "The Berlin Wall fell in which year?",
             "choices": ["1987", "1989", "1991", "1993"],
             "answer": 1, "difficulty": "medium", "tag": "20c"},
            {"q": "Which document was signed in 1215 limiting royal power in England?",
             "choices": ["Magna Carta", "Bill of Rights", "Domesday Book", "Treaty of Versailles"],
             "answer": 0, "difficulty": "medium", "tag": "medieval"},
        ],
        "Science & Nature": [
            {"q": "What is the chemical symbol for gold?",
             "choices": ["Go", "Gd", "Au", "Ag"],
             "answer": 2, "difficulty": "medium", "tag": "chemistry"},
            {"q": "How many bones are in the adult human body?",
             "choices": ["186", "206", "226", "246"],
             "answer": 1, "difficulty": "medium", "tag": "biology"},
            {"q": "What planet is known as the Red Planet?",
             "choices": ["Venus", "Mars", "Jupiter", "Saturn"],
             "answer": 1, "difficulty": "easy", "tag": "astronomy"},
            {"q": "Which gas do plants absorb from the atmosphere?",
             "choices": ["Oxygen", "Nitrogen", "Carbon Dioxide", "Helium"],
             "answer": 2, "difficulty": "easy", "tag": "biology"},
            {"q": "What is the speed of light (approx, in m/s)?",
             "choices": ["3 × 10⁵", "3 × 10⁶", "3 × 10⁸", "3 × 10¹⁰"],
             "answer": 2, "difficulty": "hard", "tag": "physics"},
            {"q": "Which animal lays the largest eggs?",
             "choices": ["Eagle", "Ostrich", "Emu", "Cassowary"],
             "answer": 1, "difficulty": "easy", "tag": "animals"},
            {"q": "What is H2O more commonly known as?",
             "choices": ["Salt", "Water", "Oxygen", "Hydrogen"],
             "answer": 1, "difficulty": "easy", "tag": "chemistry"},
            {"q": "Which is the largest planet in our solar system?",
             "choices": ["Earth", "Saturn", "Jupiter", "Neptune"],
             "answer": 2, "difficulty": "easy", "tag": "astronomy"},
        ],
        "Film & TV": [
            {"q": "Who directed 'Jurassic Park'?",
             "choices": ["James Cameron", "Steven Spielberg", "Ridley Scott", "George Lucas"],
             "answer": 1, "difficulty": "medium", "tag": "directors"},
            {"q": "What is the name of the fictional country in 'Black Panther'?",
             "choices": ["Genovia", "Wakanda", "Zamunda", "Latveria"],
             "answer": 1, "difficulty": "easy", "tag": "marvel"},
            {"q": "In 'Friends', what is Monica's job?",
             "choices": ["Chef", "Architect", "Buyer", "Journalist"],
             "answer": 0, "difficulty": "easy", "tag": "sitcom",
             "hint": "She runs a kitchen in later seasons."},
            {"q": "Which film won Best Picture at the 2020 Oscars?",
             "choices": ["1917", "Joker", "Parasite", "Once Upon a Time"],
             "answer": 2, "difficulty": "medium", "tag": "awards"},
            {"q": "What is the longest-running animated sitcom in the US?",
             "choices": ["Family Guy", "The Simpsons", "South Park", "Bob's Burgers"],
             "answer": 1, "difficulty": "easy", "tag": "animated"},
            {"q": "Who plays Iron Man in the MCU?",
             "choices": ["Chris Evans", "Mark Ruffalo", "Robert Downey Jr.", "Chris Hemsworth"],
             "answer": 2, "difficulty": "easy", "tag": "marvel"},
            {"q": "Which TV show features the phrase 'Winter is coming'?",
             "choices": ["The Witcher", "Game of Thrones", "Vikings", "Outlander"],
             "answer": 1, "difficulty": "easy", "tag": "fantasy"},
            {"q": "What is the highest-grossing film of all time (unadjusted)?",
             "choices": ["Avengers: Endgame", "Avatar", "Titanic", "Star Wars: Force Awakens"],
             "answer": 1, "difficulty": "medium", "tag": "blockbusters"},
        ],
        "Sports": [
            {"q": "How many players are on a soccer team on the field?",
             "choices": ["9", "10", "11", "12"],
             "answer": 2, "difficulty": "easy", "tag": "football"},
            {"q": "In which sport would you perform a 'slam dunk'?",
             "choices": ["Volleyball", "Tennis", "Basketball", "Handball"],
             "answer": 2, "difficulty": "easy", "tag": "court"},
            {"q": "How often are the modern Summer Olympics held?",
             "choices": ["Every 2 years", "Every 3 years", "Every 4 years", "Every 5 years"],
             "answer": 2, "difficulty": "easy", "tag": "olympics"},
            {"q": "In tennis, what does 'love' mean?",
             "choices": ["Tie", "Zero", "Fault", "Ace"],
             "answer": 1, "difficulty": "easy", "tag": "tennis"},
            {"q": "Who has won the most Tour de France titles?",
             "choices": ["Eddy Merckx", "Lance Armstrong", "Tadej Pogačar", "Miguel Indurain"],
             "answer": 0, "difficulty": "hard", "tag": "cycling",
             "hint": "Belgian, retired, 5 wins — but the answer is sometimes disputed."},
            {"q": "What is the maximum score in ten-pin bowling?",
             "choices": ["200", "250", "300", "350"],
             "answer": 2, "difficulty": "medium", "tag": "bowling"},
            {"q": "Who has the most points in NBA history?",
             "choices": ["Kareem Abdul-Jabbar", "LeBron James", "Karl Malone", "Kobe Bryant"],
             "answer": 1, "difficulty": "medium", "tag": "nba"},
            {"q": "In cricket, how many players are on a team on the field?",
             "choices": ["9", "10", "11", "12"],
             "answer": 2, "difficulty": "medium", "tag": "cricket"},
        ],
        "Food & Drink": [
            {"q": "Which spice is the most expensive by weight?",
             "choices": ["Vanilla", "Saffron", "Cardamom", "Truffle"],
             "answer": 1, "difficulty": "medium", "tag": "spices"},
            {"q": "What is the main ingredient in guacamole?",
             "choices": ["Tomato", "Avocado", "Lime", "Pepper"],
             "answer": 1, "difficulty": "easy", "tag": "mexican"},
            {"q": "Which country is famous for inventing pizza?",
             "choices": ["France", "Greece", "Italy", "Spain"],
             "answer": 2, "difficulty": "easy", "tag": "italian"},
            {"q": "How many ingredients are in a classic Negroni?",
             "choices": ["2", "3", "4", "5"],
             "answer": 1, "difficulty": "medium", "tag": "cocktails",
             "hint": "Gin, vermouth, Campari."},
            {"q": "Which fruit has the most vitamin C per 100g?",
             "choices": ["Orange", "Strawberry", "Kiwi", "Acerola (West Indian cherry)"],
             "answer": 3, "difficulty": "hard", "tag": "nutrition"},
            {"q": "Sushi originally came from which country?",
             "choices": ["China", "Korea", "Japan", "Vietnam"],
             "answer": 1, "difficulty": "hard", "tag": "asian",
             "hint": "Disputed — fermented fish & rice originated elsewhere."},
            {"q": "What country does feta cheese originate from?",
             "choices": ["Italy", "France", "Greece", "Turkey"],
             "answer": 2, "difficulty": "medium", "tag": "cheese"},
            {"q": "Which cocktail is made with rum, mint, lime, and sugar?",
             "choices": ["Old Fashioned", "Mojito", "Margarita", "Daiquiri"],
             "answer": 1, "difficulty": "easy", "tag": "cocktails"},
        ],
    },
    "music": {
        "80s Hits": [
            {"q": "Which band released 'Sweet Child O' Mine'?",
             "choices": ["Bon Jovi", "Guns N' Roses", "Def Leppard", "Mötley Crüe"],
             "answer": 1, "difficulty": "easy", "tag": "rock"},
            {"q": "Whitney Houston's 1985 debut album had how many #1 hits?",
             "choices": ["3", "5", "7", "9"],
             "answer": 2, "difficulty": "medium", "tag": "pop"},
            {"q": "Who sang 'Take On Me' (1985)?",
             "choices": ["a-ha", "Tears for Fears", "Duran Duran", "Wham!"],
             "answer": 0, "difficulty": "easy", "tag": "synth-pop"},
            {"q": "What was Michael Jackson's best-selling album of the '80s?",
             "choices": ["Off the Wall", "Bad", "Thriller", "Dangerous"],
             "answer": 2, "difficulty": "easy", "tag": "pop"},
            {"q": "Madonna's 'Like a Virgin' was released in what year?",
             "choices": ["1982", "1984", "1986", "1988"],
             "answer": 1, "difficulty": "medium", "tag": "pop"},
            {"q": "Which 80s duo had a hit with 'Ebony and Ivory' (1982)?",
             "choices": ["Hall & Oates", "Simon & Garfunkel", "McCartney & Wonder", "Wham! & George"],
             "answer": 2, "difficulty": "medium", "tag": "duets"},
            {"q": "Eurythmics' biggest 80s hit was...?",
             "choices": ["Here Comes the Rain Again", "Sweet Dreams", "Would I Lie to You", "There Must Be an Angel"],
             "answer": 1, "difficulty": "easy", "tag": "synth-pop"},
            {"q": "What instrument is Eddie Van Halen known for?",
             "choices": ["Drums", "Bass", "Guitar", "Keyboard"],
             "answer": 2, "difficulty": "easy", "tag": "rock"},
        ],
        "90s Hits": [
            {"q": "Which 1997 hit was 'MMMBop' by?",
             "choices": ["Hanson", "Backstreet Boys", "NSYNC", "LFO"],
             "answer": 0, "difficulty": "easy", "tag": "pop"},
            {"q": "Nirvana's 'Smells Like Teen Spirit' was released in which year?",
             "choices": ["1989", "1991", "1993", "1995"],
             "answer": 1, "difficulty": "medium", "tag": "grunge"},
            {"q": "Who sang 'I Want It That Way'?",
             "choices": ["98 Degrees", "Backstreet Boys", "Boyz II Men", "NSYNC"],
             "answer": 1, "difficulty": "easy", "tag": "boy-band"},
            {"q": "Tupac's 1996 double-disc album was titled...",
             "choices": ["All Eyez on Me", "Me Against the World", "Don Killuminati", "Until the End of Time"],
             "answer": 0, "difficulty": "medium", "tag": "hip-hop",
             "hint": "Double-disc classic."},
            {"q": "Which Spice Girls member was nicknamed 'Sporty'?",
             "choices": ["Mel B", "Mel C", "Victoria", "Emma"],
             "answer": 1, "difficulty": "medium", "tag": "pop"},
            {"q": "Whitney Houston's 1992 film soundtrack was...?",
             "choices": ["Waiting to Exhale", "The Bodyguard", "Preacher's Wife", "Renaissance"],
             "answer": 1, "difficulty": "easy", "tag": "soundtrack",
             "hint": "Includes 'I Will Always Love You'."},
            {"q": "Radiohead's breakthrough 1993 single was...?",
             "choices": ["Creep", "Karma Police", "No Surprises", "Lucky"],
             "answer": 0, "difficulty": "easy", "tag": "alt-rock"},
            {"q": "Which 90s act had 'Hit Me Baby, One More Time' as debut hit?",
             "choices": ["Christina Aguilera", "Britney Spears", "Jessica Simpson", "Pink"],
             "answer": 1, "difficulty": "easy", "tag": "pop"},
        ],
        "00s Hits": [
            {"q": "Which song contains 'I caught a glimpse of your smile' (2004)?",
             "choices": ["Mr. Brightside", "Somebody Told Me", "All These Things", "Smile Like You Mean It"],
             "answer": 0, "difficulty": "easy", "tag": "indie"},
            {"q": "Beyoncé's 2003 single 'Crazy in Love' features which rapper?",
             "choices": ["Jay-Z", "Kanye West", "Ludacris", "T.I."],
             "answer": 0, "difficulty": "easy", "tag": "rnb"},
            {"q": "OutKast's 'Hey Ya!' was released in which year?",
             "choices": ["2001", "2003", "2005", "2007"],
             "answer": 1, "difficulty": "medium", "tag": "hip-hop"},
            {"q": "Who is the lead singer of Coldplay?",
             "choices": ["Chris Martin", "Dave Grohl", "Thom Yorke", "Brandon Flowers"],
             "answer": 0, "difficulty": "easy", "tag": "indie"},
            {"q": "Which U2 album features 'Vertigo' (2004)?",
             "choices": ["All That You Can't Leave Behind", "How to Dismantle an Atomic Bomb", "Zooropa", "Pop"],
             "answer": 1, "difficulty": "hard", "tag": "rock"},
            {"q": "Which artist released 'Crazy in Love' (2003) and 'Single Ladies' (2008)?",
             "choices": ["Alicia Keys", "Beyoncé", "Rihanna", "Mary J. Blige"],
             "answer": 1, "difficulty": "easy", "tag": "rnb"},
            {"q": "Green Day's 2004 rock opera concept album was...",
             "choices": ["American Idiot", "Warning", "21st Century Breakdown", "Nimrod"],
             "answer": 0, "difficulty": "medium", "tag": "punk"},
            {"q": "Which 2000s duo had 'Say My Name' as a hit?",
             "choices": ["Destiny's Child", "TLC", "B2K", "Danity Kane"],
             "answer": 0, "difficulty": "easy", "tag": "rnb"},
        ],
        "Album Covers": [
            {"q": "Which band's debut album features four faces in stark color blocks on the cover?",
             "choices": ["Weezer (Blue Album)", "Radiohead (Pablo Honey)", "Nirvana (Bleach)", "Green Day (39/Smooth)"],
             "answer": 0, "difficulty": "medium", "tag": "alt-rock"},
            {"q": "Pink Floyd's 'The Dark Side of the Moon' cover is most associated with which visual element?",
             "choices": ["A prism + rainbow", "A screaming face", "Floating pigs", "An empty field"],
             "answer": 0, "difficulty": "easy", "tag": "classic-rock"},
            {"q": "The cover of Sgt. Pepper's Lonely Hearts Club Band has how many people (approx)?",
             "choices": ["30-40", "50-60", "70-80", "90-100"],
             "answer": 2, "difficulty": "hard", "tag": "beatles",
             "hint": "Famous collage of historical figures."},
            {"q": "Which album cover shows a baby swimming underwater after a dollar bill?",
             "choices": ["Nevermind (Nirvana)", "In Utero (Nirvana)", "Ten (Pearl Jam)", "Vitalogy (Pearl Jam)"],
             "answer": 0, "difficulty": "medium", "tag": "grunge"},
            {"q": "Taylor Swift's 1989 cover is most associated with which color scheme?",
             "choices": ["Polaroid", "Polaroids + cats", "Pastel blue + grid", "Black + white"],
             "answer": 0, "difficulty": "medium", "tag": "pop",
             "hint": "Polaroid-style photos of Taylor."},
            {"q": "The cover of The Beatles' 'Abbey Road' features?",
             "choices": ["Four men crossing a zebra crossing", "A yellow submarine", "A piano in a field", "Floating instruments"],
             "answer": 0, "difficulty": "easy", "tag": "beatles"},
            {"q": "Which Kanye West album has a 'dropout bear' mascot on the cover?",
             "choices": ["The College Dropout", "Late Registration", "Graduation", "808s & Heartbreak"],
             "answer": 0, "difficulty": "medium", "tag": "hip-hop"},
            {"q": "Joy Division's 'Unknown Pleasures' cover shows?",
             "choices": ["Mountain ridge", "Radio pulse waves", "A factory", "A field of stars"],
             "answer": 1, "difficulty": "hard", "tag": "post-punk"},
        ],
        "Band Lineups": [
            {"q": "How many members were in the classic Beatles lineup?",
             "choices": ["3", "4", "5", "6"],
             "answer": 1, "difficulty": "easy", "tag": "fab-four"},
            {"q": "Which Beatle died first?",
             "choices": ["John Lennon", "George Harrison", "Paul McCartney", "Ringo Starr"],
             "answer": 0, "difficulty": "easy", "tag": "fab-four"},
            {"q": "Beyoncé is a founding member of which group formed in the late '90s?",
             "choices": ["TLC", "Destiny's Child", "3LW", "The Pussycat Dolls"],
             "answer": 1, "difficulty": "easy", "tag": "rnb"},
            {"q": "How many members does BTS have?",
             "choices": ["5", "6", "7", "8"],
             "answer": 2, "difficulty": "easy", "tag": "kpop"},
            {"q": "Who is the lead singer of U2?",
             "choices": ["Bono", "The Edge", "Adam Clayton", "Larry Mullen Jr."],
             "answer": 0, "difficulty": "easy", "tag": "rock"},
            {"q": "Fleetwood Mac's classic 'Rumours' lineup had how many core members?",
             "choices": ["3", "4", "5", "6"],
             "answer": 2, "difficulty": "medium", "tag": "classic-rock"},
            {"q": "Metallica's classic 4-piece lineup dropped which bassist in 1986?",
             "choices": ["Cliff Burton", "Ron McGovney", "Dave Mustaine", "Jason Newsted"],
             "answer": 0, "difficulty": "hard", "tag": "metal",
             "hint": "Died in tour bus accident."},
            {"q": "Which K-pop group has members named RM, Jin, Suga, J-Hope...?",
             "choices": ["EXO", "BTS", "BLACKPINK", "TWICE"],
             "answer": 1, "difficulty": "easy", "tag": "kpop"},
        ],
        "Lyrics Trivia": [
            {"q": "Complete: 'Is this the real life, ___?'",
             "choices": ["is this just fantasy", "is this the end", "are we alive", "where do I stand"],
             "answer": 0, "difficulty": "easy", "tag": "queen"},
            {"q": "Who sang 'Don't stop believin'' (1981)?",
             "choices": ["REO Speedwagon", "Journey", "Styx", "Toto"],
             "answer": 1, "difficulty": "easy", "tag": "classic-rock"},
            {"q": "Fill in: 'Hello darkness, my old ___'.",
             "choices": ["friend", "pal", "buddy", "chum"],
             "answer": 0, "difficulty": "easy", "tag": "folk"},
            {"q": "Complete: 'We will, we will ___ you' (Queen).",
             "choices": ["love", "rock", "shake", "follow"],
             "answer": 1, "difficulty": "easy", "tag": "classic-rock"},
            {"q": "Who sang 'I will always love you' (1992)?",
             "choices": ["Mariah Carey", "Whitney Houston", "Celine Dion", "Tina Turner"],
             "answer": 1, "difficulty": "easy", "tag": "ballad"},
            {"q": "Fill in: 'Don't stop ___' (Journey).",
             "choices": ["the music", "the show", "the train", "moving forward"],
             "answer": 0, "difficulty": "easy", "tag": "classic-rock"},
            {"q": "Who sang 'Bohemian Rhapsody' (1975)?",
             "choices": ["Led Zeppelin", "Queen", "Pink Floyd", "The Rolling Stones"],
             "answer": 1, "difficulty": "easy", "tag": "classic-rock"},
            {"q": "Complete: 'Cause baby you're a ___' (Katy Perry).",
             "choices": ["firework", "star", "wonder", "miracle"],
             "answer": 0, "difficulty": "easy", "tag": "pop"},
        ],
        "Instruments": [
            {"q": "How many strings does a violin have?",
             "choices": ["3", "4", "5", "6"],
             "answer": 1, "difficulty": "easy", "tag": "instruments"},
            {"q": "Which instrument is NOT a brass instrument?",
             "choices": ["Trumpet", "Trombone", "Clarinet", "Tuba"],
             "answer": 2, "difficulty": "medium", "tag": "instruments"},
            {"q": "A piano has how many keys in total?",
             "choices": ["76", "84", "88", "92"],
             "answer": 2, "difficulty": "medium", "tag": "instruments"},
            {"q": "Which instrument family does the violin belong to?",
             "choices": ["Woodwind", "String", "Brass", "Percussion"],
             "answer": 1, "difficulty": "easy", "tag": "instruments"},
            {"q": "How many keys does a standard saxophone have?",
             "choices": ["18", "20", "23", "26"],
             "answer": 2, "difficulty": "hard", "tag": "instruments"},
            {"q": "The harmonica belongs to which family?",
             "choices": ["Brass", "Woodwind", "Free reed", "Percussion"],
             "answer": 2, "difficulty": "hard", "tag": "instruments"},
            {"q": "Drum kits use which type of cymbal to keep time?",
             "choices": ["Crash", "Ride", "Hi-hat", "Splash"],
             "answer": 2, "difficulty": "medium", "tag": "instruments"},
            {"q": "How many valves does a standard trumpet have?",
             "choices": ["2", "3", "4", "5"],
             "answer": 1, "difficulty": "medium", "tag": "instruments"},
        ],
    },
    "movie-tv": {
        "Blockbusters": [
            {"q": "Which actor has played James Bond the most times?",
             "choices": ["Sean Connery", "Roger Moore", "Daniel Craig", "Pierce Brosnan"],
             "answer": 1, "difficulty": "easy", "tag": "bond",
             "hint": "Seven films between 1973 and 1985."},
            {"q": "The first 'Toy Story' was released in which year?",
             "choices": ["1993", "1995", "1997", "1999"],
             "answer": 1, "difficulty": "medium", "tag": "pixar"},
            {"q": "Who directed 'The Godfather'?",
             "choices": ["Martin Scorsese", "Francis Ford Coppola", "Steven Spielberg", "Brian De Palma"],
             "answer": 1, "difficulty": "medium", "tag": "classics"},
            {"q": "Which franchise features 'Wookiees' and the Millennium Falcon?",
             "choices": ["Star Trek", "Star Wars", "Guardians of the Galaxy", "Dune"],
             "answer": 1, "difficulty": "easy", "tag": "star-wars"},
            {"q": "What is the highest-grossing film of all time (unadjusted)?",
             "choices": ["Avengers: Endgame", "Avatar", "Titanic", "Star Wars: Force Awakens"],
             "answer": 1, "difficulty": "medium", "tag": "blockbusters"},
            {"q": "In 'Lord of the Rings', who carries the One Ring to Mount Doom?",
             "choices": ["Aragorn", "Frodo", "Sam", "Gandalf"],
             "answer": 1, "difficulty": "easy", "tag": "fantasy"},
            {"q": "Which Marvel film features Thanos as the central villain?",
             "choices": ["Avengers: Age of Ultron", "Avengers: Infinity War", "Black Panther", "Doctor Strange"],
             "answer": 1, "difficulty": "medium", "tag": "marvel"},
            {"q": "What real-life lawyer wrote 'Dream Team' and 'Runnin' Down a Dream'?",
             "choices": ["Alan Dershowitz", "Johnnie Cochran", "Mark Geragos", "Tom Mesereau"],
             "answer": 2, "difficulty": "hard", "tag": "docs",
             "hint": "Disciplinary journalist on the Michael Jackson trials."},
        ],
        "TV Sitcoms": [
            {"q": "In 'Seinfeld', what is George's last name?",
             "choices": ["Costanza", "Kramer", "Seinfeld", "Benes"],
             "answer": 0, "difficulty": "easy", "tag": "90s"},
            {"q": "The Big Bang Theory: what instrument does Sheldon play?",
             "choices": ["Piano", "Theremin", "Violin", "Recorder"],
             "answer": 1, "difficulty": "medium", "tag": "00s"},
            {"q": "Which sitcom character says 'How you doin'?'",
             "choices": ["Joey Tribbiani", "Chandler Bing", "Ross Geller", "Monica Geller"],
             "answer": 0, "difficulty": "easy", "tag": "friends"},
            {"q": "'D'oh!' is the catchphrase of which cartoon dad?",
             "choices": ["Peter Griffin", "Homer Simpson", "Randy Marsh", "Stan Smith"],
             "answer": 1, "difficulty": "easy", "tag": "animated"},
            {"q": "Which sitcom is set in a Boston bar owned by Sam Malone?",
             "choices": ["Cheers", "Friends", "Seinfeld", "How I Met Your Mother"],
             "answer": 0, "difficulty": "easy", "tag": "80s"},
            {"q": "In 'The Office' (US), what company do the characters work for?",
             "choices": ["Initech", "Dunder Mifflin", "Pied Piper", "Sterling Cooper"],
             "answer": 1, "difficulty": "easy", "tag": "00s"},
            {"q": "Which show features a character called 'Stewie Griffin'?",
             "choices": ["Family Guy", "American Dad", "The Cleveland Show", "King of the Hill"],
             "answer": 0, "difficulty": "easy", "tag": "animated"},
            {"q": "'Schitt's Creek' features a family that loses its...?",
             "choices": ["House", "Fortune", "Passport", "Identity"],
             "answer": 1, "difficulty": "easy", "tag": "modern"},
        ],
        "Movie Quotes": [
            {"q": "'I'll be back' is from which film?",
             "choices": ["RoboCop", "Terminator", "Predator", "Commando"],
             "answer": 1, "difficulty": "easy", "tag": "action"},
            {"q": "'Houston, we have a problem' is from which film?",
             "choices": ["Apollo 11", "Apollo 13", "First Man", "Gravity"],
             "answer": 1, "difficulty": "easy", "tag": "space"},
            {"q": "'You can't handle the truth!' is from which film?",
             "choices": ["A Few Good Men", "The Firm", "Philadelphia", "The Pelican Brief"],
             "answer": 0, "difficulty": "medium", "tag": "drama"},
            {"q": "'May the Force be with you' is from which franchise?",
             "choices": ["Star Trek", "Star Wars", "Dune", "Foundation"],
             "answer": 1, "difficulty": "easy", "tag": "star-wars"},
            {"q": "'Elementary, my dear Watson' is associated with which character?",
             "choices": ["Hercule Poirot", "Sherlock Holmes", "Philip Marlowe", "Miss Marple"],
             "answer": 1, "difficulty": "easy", "tag": "mystery"},
            {"q": "'Why so serious?' is from which film?",
             "choices": ["The Dark Knight", "Joker", "Batman Begins", "Suicide Squad"],
             "answer": 0, "difficulty": "easy", "tag": "comics"},
            {"q": "'To infinity and beyond!' is a quote from which film?",
             "choices": ["Finding Nemo", "Toy Story", "The Incredibles", "Cars"],
             "answer": 1, "difficulty": "easy", "tag": "pixar"},
            {"q": "'I see dead people' is a line from which film?",
             "choices": ["The Others", "The Sixth Sense", "Ghost", "Ghostbusters"],
             "answer": 1, "difficulty": "easy", "tag": "thriller"},
        ],
        "Directors": [
            {"q": "Who directed 'Pulp Fiction'?",
             "choices": ["Quentin Tarantino", "Robert Rodriguez", "David Fincher", "Coen Brothers"],
             "answer": 0, "difficulty": "easy", "tag": "90s"},
            {"q": "Christopher Nolan directed all of these EXCEPT:",
             "choices": ["Inception", "Interstellar", "Dunkirk", "Arrival"],
             "answer": 3, "difficulty": "medium", "tag": "modern",
             "hint": "Denis Villeneuve's film."},
            {"q": "Greta Gerwig directed...",
             "choices": ["Lady Bird", "Little Women", "Barbie", "All of the above"],
             "answer": 3, "difficulty": "easy", "tag": "modern"},
            {"q": "Steven Spielberg directed which of these?",
             "choices": ["Jurassic Park", "Schindler's List", "Saving Private Ryan", "All of the above"],
             "answer": 3, "difficulty": "easy", "tag": "directors"},
            {"q": "Who directed the 'Lord of the Rings' trilogy?",
             "choices": ["Guillermo del Toro", "Peter Jackson", "Alfonso Cuarón", "Taika Waititi"],
             "answer": 1, "difficulty": "easy", "tag": "fantasy"},
            {"q": "Martin Scorsese directed which of these?",
             "choices": ["Goodfellas", "Taxi Driver", "The Departed", "All of the above"],
             "answer": 3, "difficulty": "easy", "tag": "directors"},
            {"q": "Who directed 'The Grand Budapest Hotel'?",
             "choices": ["Wes Anderson", "Noah Baumbach", "Paul Thomas Anderson", "Sofia Coppola"],
             "answer": 0, "difficulty": "medium", "tag": "indie"},
            {"q": "Ridley Scott directed which sci-fi classic?",
             "choices": ["2001", "Blade Runner", "The Matrix", "Arrival"],
             "answer": 1, "difficulty": "medium", "tag": "scifi"},
        ],
        "Sci-Fi": [
            {"q": "Which 'Star Wars' film is chronologically FIRST in the saga?",
             "choices": ["A New Hope", "The Phantom Menace", "Revenge of the Sith", "Rogue One"],
             "answer": 1, "difficulty": "medium", "tag": "star-wars"},
            {"q": "In 'The Matrix', what color pill does Neo take?",
             "choices": ["Blue", "Red", "Green", "Yellow"],
             "answer": 1, "difficulty": "easy", "tag": "90s"},
            {"q": "Who wrote '2001: A Space Odyssey' (the novel)?",
             "choices": ["Isaac Asimov", "Arthur C. Clarke", "Ray Bradbury", "Philip K. Dick"],
             "answer": 1, "difficulty": "medium", "tag": "classic-sci-fi"},
            {"q": "In 'Blade Runner', what is the term for the bioengineered humans?",
             "choices": ["Replicants", "Androids", "Synthetics", "Clones"],
             "answer": 0, "difficulty": "medium", "tag": "scifi"},
            {"q": "Which sci-fi film features 'HAL 9000'?",
             "choices": ["Star Trek", "Alien", "2001: A Space Odyssey", "Interstellar"],
             "answer": 2, "difficulty": "easy", "tag": "scifi"},
            {"q": "In 'Back to the Future', what speed does the DeLorean need to reach?",
             "choices": ["55 mph", "88 mph", "100 mph", "120 mph"],
             "answer": 1, "difficulty": "easy", "tag": "scifi"},
            {"q": "Who directed 'Arrival' (2016)?",
             "choices": ["Denis Villeneuve", "Christopher Nolan", "Ridley Scott", "David Fincher"],
             "answer": 0, "difficulty": "medium", "tag": "modern"},
            {"q": "In 'Star Trek', the catchphrase is...?",
             "choices": ["Live long and prosper", "May the Force", "To boldly go", "Engage"],
             "answer": 0, "difficulty": "easy", "tag": "star-trek"},
        ],
    },
    "sports": {
        "Football (Soccer)": [
            {"q": "Which country has won the most FIFA World Cups?",
             "choices": ["Germany", "Argentina", "Brazil", "Italy"],
             "answer": 2, "difficulty": "easy", "tag": "world-cup"},
            {"q": "How many minutes is a standard soccer match (not including stoppage)?",
             "choices": ["80", "90", "100", "120"],
             "answer": 1, "difficulty": "easy", "tag": "rules"},
            {"q": "In which year did the first Premier League season kick off?",
             "choices": ["1990", "1992", "1994", "1996"],
             "answer": 1, "difficulty": "medium", "tag": "england"},
            {"q": "What is the nickname of Manchester United?",
             "choices": ["The Blues", "The Reds", "Red Devils", "Gunners"],
             "answer": 2, "difficulty": "easy", "tag": "england"},
            {"q": "Which player has won the most Ballon d'Or awards (men)?",
             "choices": ["Cristiano Ronaldo", "Lionel Messi", "Michel Platini", "Johan Cruyff"],
             "answer": 1, "difficulty": "medium", "tag": "individual"},
            {"q": "What is the name of the Spanish national soccer league?",
             "choices": ["Serie A", "Bundesliga", "La Liga", "Ligue 1"],
             "answer": 2, "difficulty": "easy", "tag": "leagues"},
            {"q": "Which country hosted the 2022 FIFA World Cup?",
             "choices": ["Russia", "Qatar", "Saudi Arabia", "UAE"],
             "answer": 1, "difficulty": "easy", "tag": "world-cup"},
            {"q": "Which position wears the number 1 in soccer?",
             "choices": ["Striker", "Goalkeeper", "Captain", "Defender"],
             "answer": 1, "difficulty": "easy", "tag": "rules"},
        ],
        "Olympics": [
            {"q": "Where were the 2024 Summer Olympics held?",
             "choices": ["Tokyo", "Paris", "Los Angeles", "Brisbane"],
             "answer": 1, "difficulty": "easy", "tag": "modern"},
            {"q": "Which city has hosted the Olympics THREE times (both Summer and Winter)?",
             "choices": ["London", "Tokyo", "Paris", "Athens"],
             "answer": 2, "difficulty": "hard", "tag": "history",
             "hint": "1900, 1924, 2024."},
            {"q": "Which country has won the most Summer Olympic medals all-time?",
             "choices": ["China", "USA", "Russia", "Germany"],
             "answer": 1, "difficulty": "easy", "tag": "alltime"},
            {"q": "The Olympic rings represent how many continents?",
             "choices": ["4", "5", "6", "7"],
             "answer": 1, "difficulty": "medium", "tag": "tradition"},
            {"q": "In which year were the first modern Olympics held?",
             "choices": ["1888", "1896", "1900", "1912"],
             "answer": 1, "difficulty": "medium", "tag": "history"},
            {"q": "Which city will host the 2028 Summer Olympics?",
             "choices": ["Tokyo", "Paris", "Los Angeles", "Brisbane"],
             "answer": 2, "difficulty": "easy", "tag": "modern"},
            {"q": "Which country invented the Olympics?",
             "choices": ["Italy", "Greece", "Egypt", "Turkey"],
             "answer": 1, "difficulty": "easy", "tag": "history"},
            {"q": "The Summer Olympics are held every how many years?",
             "choices": ["2", "3", "4", "5"],
             "answer": 2, "difficulty": "easy", "tag": "basics"},
        ],
        "Tennis": [
            {"q": "Which surface is the French Open played on?",
             "choices": ["Grass", "Clay", "Hard", "Carpet"],
             "answer": 1, "difficulty": "easy", "tag": "slams"},
            {"q": "How many Grand Slams has Serena Williams won (singles)?",
             "choices": ["21", "22", "23", "24"],
             "answer": 2, "difficulty": "medium", "tag": "womens"},
            {"q": "Roger Federer has won how many Grand Slams (singles)?",
             "choices": ["18", "20", "22", "24"],
             "answer": 1, "difficulty": "hard", "tag": "mens"},
            {"q": "Which tournament is NOT a Grand Slam?",
             "choices": ["Wimbledon", "US Open", "Indian Wells", "Australian Open"],
             "answer": 2, "difficulty": "medium", "tag": "tournaments"},
            {"q": "In tennis, what does a score of 15, 30, 40, Game represent?",
             "choices": ["Set", "Match", "Game", "Tiebreaker"],
             "answer": 2, "difficulty": "easy", "tag": "rules"},
            {"q": "Who has the most men's Grand Slam titles as of 2025?",
             "choices": ["Roger Federer", "Rafael Nadal", "Novak Djokovic", "Pete Sampras"],
             "answer": 2, "difficulty": "medium", "tag": "mens"},
            {"q": "Wimbledon is the only Grand Slam played on which surface?",
             "choices": ["Clay", "Hard", "Grass", "Indoor"],
             "answer": 2, "difficulty": "easy", "tag": "slams"},
            {"q": "What does 'deuce' mean in tennis?",
             "choices": ["Tied at 40-40", "Match point", "Set point", "First serve"],
             "answer": 0, "difficulty": "medium", "tag": "rules"},
        ],
        "NFL": [
            {"q": "How many points is a touchdown worth?",
             "choices": ["3", "6", "7", "8"],
             "answer": 1, "difficulty": "easy", "tag": "rules"},
            {"q": "Which team has won the most Super Bowls?",
             "choices": ["Cowboys", "Steelers", "Patriots", "Packers"],
             "answer": 2, "difficulty": "easy", "tag": "history"},
            {"q": "How many players are on a team on the field in American football?",
             "choices": ["9", "10", "11", "12"],
             "answer": 2, "difficulty": "easy", "tag": "rules"},
            {"q": "Which NFL team is based in Green Bay?",
             "choices": ["Packers", "Bears", "Vikings", "Lions"],
             "answer": 0, "difficulty": "easy", "tag": "history"},
            {"q": "How long is an NFL game (regulation)?",
             "choices": ["48 minutes", "60 minutes", "75 minutes", "90 minutes"],
             "answer": 1, "difficulty": "medium", "tag": "rules"},
            {"q": "Who has won the most Super Bowls as a player?",
             "choices": ["Tom Brady", "Joe Montana", "Charles Haley", "Terry Bradshaw"],
             "answer": 0, "difficulty": "medium", "tag": "individual"},
            {"q": "Which team lost the first Super Bowl?",
             "choices": ["Packers", "Chiefs", "Vikings", "Colts"],
             "answer": 1, "difficulty": "hard", "tag": "history"},
            {"q": "NFL 'Overtime' regular season rules: each team gets...",
             "choices": ["1 possession", "2 possessions", "Unlimited", "10 minutes"],
             "answer": 0, "difficulty": "medium", "tag": "rules"},
        ],
        "NBA": [
            {"q": "How many players from one team are on the basketball court?",
             "choices": ["4", "5", "6", "7"],
             "answer": 1, "difficulty": "easy", "tag": "rules"},
            {"q": "How high is an NBA basketball hoop?",
             "choices": ["9 feet", "10 feet", "11 feet", "12 feet"],
             "answer": 1, "difficulty": "easy", "tag": "rules"},
            {"q": "Who has scored the most points in NBA history?",
             "choices": ["Kareem Abdul-Jabbar", "LeBron James", "Karl Malone", "Kobe Bryant"],
             "answer": 1, "difficulty": "medium", "tag": "individual"},
            {"q": "How long is an NBA game (regulation)?",
             "choices": ["32 minutes", "48 minutes", "60 minutes", "75 minutes"],
             "answer": 1, "difficulty": "medium", "tag": "rules"},
            {"q": "Which NBA team is based in Los Angeles?",
             "choices": ["Lakers", "Clippers", "Both of the above", "Kings"],
             "answer": 2, "difficulty": "easy", "tag": "cities"},
            {"q": "How many quarters are in an NBA game?",
             "choices": ["2", "3", "4", "5"],
             "answer": 2, "difficulty": "easy", "tag": "rules"},
            {"q": "Which team drafted Michael Jordan?",
             "choices": ["Bulls", "Cavaliers", "Knicks", "Heat"],
             "answer": 0, "difficulty": "easy", "tag": "individual",
             "hint": "Their 3rd pick in 1984."},
            {"q": "Stephen Curry is best known for which shot?",
             "choices": ["Free throw", "Dunk", "Three-pointer", "Lay-up"],
             "answer": 2, "difficulty": "easy", "tag": "individual"},
        ],
        "World Cup Records": [
            {"q": "Which country has won the most FIFA World Cups?",
             "choices": ["Brazil", "Germany", "Italy", "Argentina"],
             "answer": 0, "difficulty": "easy", "tag": "records"},
            {"q": "Miroslav Klose holds the record for most World Cup goals (16). His nationality?",
             "choices": ["Brazil", "Poland", "Germany", "Argentina"],
             "answer": 2, "difficulty": "medium", "tag": "records"},
            {"q": "How many players are on a country's World Cup squad?",
             "choices": ["21", "23", "26", "30"],
             "answer": 2, "difficulty": "medium", "tag": "modern",
             "hint": "As of 2022 rule change."},
            {"q": "Which country won the 2018 FIFA World Cup?",
             "choices": ["Brazil", "Croatia", "France", "Germany"],
             "answer": 2, "difficulty": "easy", "tag": "history"},
            {"q": "Who scored the 'Hand of God' goal (1986)?",
             "choices": ["Pelé", "Maradona", "Ronaldo", "Zidane"],
             "answer": 1, "difficulty": "medium", "tag": "history"},
            {"q": "Zidane's headbutt in the 2006 final was against which player?",
             "choices": ["Cannavaro", "Buffon", "Materazzi", "Totti"],
             "answer": 2, "difficulty": "medium", "tag": "history"},
            {"q": "Which country has won 2 World Cups but never hosted?",
             "choices": ["France", "Argentina", "Spain", "England"],
             "answer": 1, "difficulty": "hard", "tag": "records"},
            {"q": "Who is the all-time top scorer in World Cup history?",
             "choices": ["Pelé", "Klose", "Ronaldo", "Müller"],
             "answer": 1, "difficulty": "medium", "tag": "records"},
        ],
        "Boxing & MMA": [
            {"q": "How many rounds are in a standard championship boxing match?",
             "choices": ["8", "10", "12", "15"],
             "answer": 2, "difficulty": "medium", "tag": "boxing"},
            {"q": "MMA stands for...?",
             "choices": ["Mixed Martial Arts", "Modern Martial Arts", "Multi-Match Athletic", "Maximum Martial Arts"],
             "answer": 0, "difficulty": "easy", "tag": "mma"},
            {"q": "Which boxer was known as 'The Greatest'?",
             "choices": ["Ali", "Tyson", "Floyd", "Pacquiao"],
             "answer": 0, "difficulty": "easy", "tag": "boxing"},
            {"q": "Conor McGregor is best known for fighting in which promotion?",
             "choices": ["UFC", "Bellator", "ONE", "PFL"],
             "answer": 0, "difficulty": "easy", "tag": "mma"},
            {"q": "Mike Tyson was famous for knocking people out in how many rounds?",
             "choices": ["Round 1", "Rounds 1-2", "Rounds 8-10", "Always the last"],
             "answer": 1, "difficulty": "medium", "tag": "boxing"},
            {"q": "How many weight classes are in the UFC (approximately)?",
             "choices": ["4", "8", "12", "16"],
             "answer": 2, "difficulty": "hard", "tag": "mma"},
            {"q": "Which fighter holds the UFC record for most title defenses?",
             "choices": ["Amanda Nunes", "Demetrious Johnson", "Jon Jones", "GSP"],
             "answer": 1, "difficulty": "hard", "tag": "mma"},
            {"q": "Floyd Mayweather is known for being...?",
             "choices": ["Undefeated", "Heavyweight champion", "Olympic gold", "Fastest KO"],
             "answer": 0, "difficulty": "easy", "tag": "boxing"},
        ],
        "Stadiums & Venues": [
            {"q": "Where is the Wimbledon tennis tournament held?",
             "choices": ["Wimbledon", "Queen's Club", "All England Club", "St. Andrews"],
             "answer": 2, "difficulty": "easy", "tag": "tennis"},
            {"q": "What is the home stadium of FC Barcelona called?",
             "choices": ["Santiago Bernabéu", "Camp Nou", "MetLife", "Old Trafford"],
             "answer": 1, "difficulty": "easy", "tag": "soccer"},
            {"q": "Wembley Stadium is in which country?",
             "choices": ["Scotland", "England", "Wales", "Ireland"],
             "answer": 1, "difficulty": "easy", "tag": "soccer"},
            {"q": "Fenway Park is the home of which MLB team?",
             "choices": ["Yankees", "Red Sox", "Cubs", "Giants"],
             "answer": 1, "difficulty": "easy", "tag": "baseball"},
            {"q": "Madison Square Garden is in which city?",
             "choices": ["LA", "NY", "Chicago", "Boston"],
             "answer": 1, "difficulty": "easy", "tag": "basketball"},
            {"q": "The Super Bowl is typically played in a...?",
             "choices": ["Football stadium", "Indoor arena", "Soccer stadium", "University stadium"],
             "answer": 0, "difficulty": "medium", "tag": "nfl"},
            {"q": "Staples Center became which name in 2021?",
             "choices": ["Crypto.com Arena", "Intuit Dome", "SoFi Center", "Lakers Arena"],
             "answer": 0, "difficulty": "medium", "tag": "nba"},
            {"q": "Old Trafford is the home of which club?",
             "choices": ["Liverpool", "Man City", "Man United", "Chelsea"],
             "answer": 2, "difficulty": "easy", "tag": "soccer"},
        ],
    },
    "geography": {
        "Capital Cities": [
            {"q": "What is the capital of Canada?",
             "choices": ["Toronto", "Ottawa", "Vancouver", "Montreal"],
             "answer": 1, "difficulty": "easy", "tag": "americas"},
            {"q": "What is the capital of New Zealand?",
             "choices": ["Auckland", "Christchurch", "Wellington", "Dunedin"],
             "answer": 2, "difficulty": "easy", "tag": "oceania"},
            {"q": "What is the capital of Brazil?",
             "choices": ["São Paulo", "Rio de Janeiro", "Brasília", "Salvador"],
             "answer": 2, "difficulty": "medium", "tag": "americas"},
            {"q": "What is the capital of South Africa (legislative)?",
             "choices": ["Pretoria", "Cape Town", "Johannesburg", "Bloemfontein"],
             "answer": 1, "difficulty": "hard", "tag": "africa",
             "hint": "It has three official capitals."},
            {"q": "What is the capital of Spain?",
             "choices": ["Barcelona", "Madrid", "Valencia", "Seville"],
             "answer": 1, "difficulty": "easy", "tag": "europe"},
            {"q": "What is the capital of Norway?",
             "choices": ["Bergen", "Stockholm", "Oslo", "Helsinki"],
             "answer": 2, "difficulty": "easy", "tag": "europe"},
            {"q": "What is the capital of Thailand?",
             "choices": ["Bangkok", "Chiang Mai", "Phuket", "Pattaya"],
             "answer": 0, "difficulty": "easy", "tag": "asia"},
            {"q": "What is the capital of Egypt?",
             "choices": ["Cairo", "Alexandria", "Luxor", "Giza"],
             "answer": 0, "difficulty": "easy", "tag": "africa"},
        ],
        "Flags": [
            {"q": "Which country's flag is a solid red field with a single yellow star?",
             "choices": ["China", "Vietnam", "Myanmar", "Turkey"],
             "answer": 1, "difficulty": "medium", "tag": "asia"},
            {"q": "The Union Jack is the flag of which countries?",
             "choices": ["UK only", "UK, Scotland, Wales", "UK, England, Scotland, N. Ireland", "England + UK"],
             "answer": 2, "difficulty": "medium", "tag": "uk"},
            {"q": "Which country's flag has a maple leaf?",
             "choices": ["USA", "Canada", "Mexico", "Finland"],
             "answer": 1, "difficulty": "easy", "tag": "americas"},
            {"q": "The colors of the German flag are?",
             "choices": ["Red/white/blue", "Black/red/gold", "Green/white/red", "Black/white/red"],
             "answer": 1, "difficulty": "easy", "tag": "europe"},
            {"q": "The Japanese flag is a red circle on a...?",
             "choices": ["White field", "Blue field", "Black field", "Red field"],
             "answer": 0, "difficulty": "easy", "tag": "asia"},
            {"q": "The Italian flag is...?",
             "choices": ["Blue/white/red vertical", "Green/white/red vertical", "Red/white/green vertical", "Black/white/red vertical"],
             "answer": 1, "difficulty": "easy", "tag": "europe"},
            {"q": "How many stars are on the USA flag?",
             "choices": ["48", "50", "52", "51"],
             "answer": 1, "difficulty": "easy", "tag": "americas"},
            {"q": "Brazil's flag features which animal?",
             "choices": ["Eagle", "Jaguar", "Macaw", "Armadillo"],
             "answer": 1, "difficulty": "medium", "tag": "americas",
             "hint": "Cat-like apex predator."},
        ],
        "Maps & Mountains": [
            {"q": "What is the highest mountain in Africa?",
             "choices": ["Mt. Kenya", "Mt. Kilimanjaro", "Mt. Stanley", "Mt. Speke"],
             "answer": 1, "difficulty": "easy", "tag": "africa"},
            {"q": "The Andes run through how many countries?",
             "choices": ["3", "5", "7", "9"],
             "answer": 2, "difficulty": "hard", "tag": "americas",
             "hint": "Includes Venezuela, Colombia, Ecuador, Peru, Bolivia, Chile, Argentina."},
            {"q": "How many continents are there (most common model)?",
             "choices": ["5", "6", "7", "8"],
             "answer": 2, "difficulty": "easy", "tag": "basics"},
            {"q": "Which is the longest mountain range in the world?",
             "choices": ["Rockies", "Alps", "Andes", "Himalayas"],
             "answer": 2, "difficulty": "medium", "tag": "mountains"},
            {"q": "Mount Everest sits on the border of which two countries?",
             "choices": ["Nepal / China", "India / China", "Pakistan / India", "Nepal / India"],
             "answer": 0, "difficulty": "easy", "tag": "mountains"},
            {"q": "What is the highest mountain in North America?",
             "choices": ["Mt. Whitney", "Denali (Mt. McKinley)", "Mt. Logan", "Pico de Orizaba"],
             "answer": 1, "difficulty": "medium", "tag": "americas"},
            {"q": "Which mountain range separates Europe from Asia?",
             "choices": ["Alps", "Pyrenees", "Urals", "Carpathians"],
             "answer": 2, "difficulty": "medium", "tag": "europe"},
            {"q": "K2 is the second-highest mountain after Everest. Which country is it primarily in?",
             "choices": ["Nepal", "China", "Pakistan", "India"],
             "answer": 2, "difficulty": "hard", "tag": "mountains"},
        ],
        "Currencies": [
            {"q": "Which country uses the yen?",
             "choices": ["China", "South Korea", "Japan", "Thailand"],
             "answer": 2, "difficulty": "easy", "tag": "asia"},
            {"q": "What is the currency of Switzerland?",
             "choices": ["Euro", "Swiss franc", "Kroner", "Pound"],
             "answer": 1, "difficulty": "easy", "tag": "europe"},
            {"q": "Which African country's currency is the kwacha?",
             "choices": ["Kenya", "Zambia", "Nigeria", "Egypt"],
             "answer": 1, "difficulty": "hard", "tag": "africa",
             "hint": "Also Malawi."},
            {"q": "Which country uses the peso as its primary currency?",
             "choices": ["Brazil", "Mexico", "Peru", "All of the above"],
             "answer": 3, "difficulty": "medium", "tag": "americas"},
            {"q": "Which country uses the rupee?",
             "choices": ["India", "Bangladesh", "Sri Lanka", "All of the above"],
             "answer": 3, "difficulty": "medium", "tag": "asia"},
            {"q": "Which country's currency is the krona?",
             "choices": ["Denmark", "Sweden", "Norway", "All of the above"],
             "answer": 3, "difficulty": "medium", "tag": "europe"},
            {"q": "What is the currency of Turkey?",
             "choices": ["Dinar", "Lira", "Pound", "Riyal"],
             "answer": 1, "difficulty": "medium", "tag": "middle-east"},
            {"q": "The euro is used in roughly how many EU countries?",
             "choices": ["12", "16", "20", "24"],
             "answer": 2, "difficulty": "medium", "tag": "europe"},
        ],
    },
    "tech": {
        "Internet History": [
            {"q": "What year was the first website published?",
             "choices": ["1989", "1991", "1994", "1996"],
             "answer": 1, "difficulty": "medium", "tag": "history",
             "hint": "At CERN."},
            {"q": "What does 'http' stand for?",
             "choices": ["HyperText Transfer Protocol", "Hyperlink Test Page", "Home Tool Text", "HyperText Tracking Page"],
             "answer": 0, "difficulty": "easy", "tag": "basics"},
            {"q": "Who is widely credited as the inventor of the World Wide Web?",
             "choices": ["Vint Cerf", "Tim Berners-Lee", "Bob Kahn", "Marc Andreessen"],
             "answer": 1, "difficulty": "easy", "tag": "founders"},
            {"q": "What does 'URL' stand for?",
             "choices": ["Universal Resource Link", "Uniform Resource Locator", "Unified Reference Layer", "Universal Routing Logic"],
             "answer": 1, "difficulty": "easy", "tag": "basics"},
            {"q": "When did Google launch?",
             "choices": ["1996", "1998", "2000", "2002"],
             "answer": 1, "difficulty": "medium", "tag": "history"},
            {"q": "What does 'WWW' originally stand for?",
             "choices": ["World Wide Web", "World Web Works", "Wide World Web", "Web World Wide"],
             "answer": 0, "difficulty": "easy", "tag": "basics"},
            {"q": "The first commercially available web browser was...?",
             "choices": ["Mosaic", "Netscape Navigator", "Internet Explorer", "Safari"],
             "answer": 1, "difficulty": "hard", "tag": "history"},
            {"q": "Email was invented in the 1970s by whom?",
             "choices": ["Ray Tomlinson", "Vint Cerf", "Tim Berners-Lee", "Steve Wozniak"],
             "answer": 0, "difficulty": "hard", "tag": "history"},
        ],
        "Tech Founders": [
            {"q": "Who co-founded Apple with Steve Jobs?",
             "choices": ["Steve Wozniak", "Steve Ballmer", "Bill Gates", "Mark Zuckerberg"],
             "answer": 0, "difficulty": "easy", "tag": "founders"},
            {"q": "Tesla's CEO (as of 2025) is...?",
             "choices": ["Martin Eberhard", "Elon Musk", "JB Straubel", "Tony Stark"],
             "answer": 1, "difficulty": "easy", "tag": "founders"},
            {"q": "Amazon was founded in which year?",
             "choices": ["1990", "1994", "1998", "2002"],
             "answer": 1, "difficulty": "medium", "tag": "founders"},
            {"q": "Who founded Microsoft?",
             "choices": ["Bill Gates + Steve Ballmer", "Bill Gates + Paul Allen", "Steve Jobs + Steve Wozniak", "Mark Zuckerberg + Eduardo Saverin"],
             "answer": 1, "difficulty": "easy", "tag": "founders"},
            {"q": "Mark Zuckerberg is the founder of...?",
             "choices": ["Twitter", "Facebook/Meta", "Snapchat", "Instagram"],
             "answer": 1, "difficulty": "easy", "tag": "founders"},
            {"q": "Which company did Larry Page and Sergey Brin found?",
             "choices": ["Yahoo", "Google", "Bing", "Baidu"],
             "answer": 1, "difficulty": "easy", "tag": "founders"},
            {"q": "Who founded SpaceX?",
             "choices": ["Jeff Bezos", "Richard Branson", "Elon Musk", "Paul Allen"],
             "answer": 2, "difficulty": "easy", "tag": "founders"},
            {"q": "Who founded Facebook's predecessor at Harvard?",
             "choices": ["Eduardo Saverin", "Dustin Moskovitz", "Mark Zuckerberg", "Andrew McCollum"],
             "answer": 2, "difficulty": "medium", "tag": "founders"},
        ],
        "Video Games": [
            {"q": "Which company created Mario?",
             "choices": ["Sega", "Sony", "Nintendo", "Atari"],
             "answer": 2, "difficulty": "easy", "tag": "gaming"},
            {"q": "Minecraft was released in which year?",
             "choices": ["2007", "2009", "2011", "2013"],
             "answer": 2, "difficulty": "medium", "tag": "gaming"},
            {"q": "Which game features the character 'Master Chief'?",
             "choices": ["Destiny", "Halo", "DOOM", "Call of Duty"],
             "answer": 1, "difficulty": "easy", "tag": "gaming"},
            {"q": "Which is the best-selling video game of all time (as of 2025)?",
             "choices": ["Tetris", "Minecraft", "GTA V", "Wii Sports"],
             "answer": 1, "difficulty": "medium", "tag": "gaming"},
            {"q": "Sonic the Hedgehog's main rival is...?",
             "choices": ["Mario", "Crash Bandicoot", "Dr. Robotnik", "Tails"],
             "answer": 2, "difficulty": "easy", "tag": "gaming"},
            {"q": "The game 'Fortnite' was made by which studio?",
             "choices": ["Epic Games", "Activision", "EA", "Blizzard"],
             "answer": 0, "difficulty": "easy", "tag": "gaming"},
            {"q": "What console is famous for the character 'Link'?",
             "choices": ["PlayStation", "Xbox", "Nintendo", "Sega"],
             "answer": 2, "difficulty": "easy", "tag": "gaming"},
            {"q": "The 'Battle Royale' genre became huge partly due to which 2017 game?",
             "choices": ["PUBG", "Fortnite", "Apex Legends", "Call of Duty: Warzone"],
             "answer": 1, "difficulty": "medium", "tag": "gaming"},
        ],
        "App & Software": [
            {"q": "Slack is best described as a...",
             "choices": ["Video editor", "Team messaging tool", "Spreadsheet", "Browser"],
             "answer": 1, "difficulty": "easy", "tag": "tools"},
            {"q": "An 'API' stands for...",
             "choices": ["Application Programming Interface", "Active Process Input", "App Page Index", "Automated Program Interface"],
             "answer": 0, "difficulty": "easy", "tag": "basics"},
            {"q": "Git was originally created by whom?",
             "choices": ["Linus Torvalds", "Guido van Rossum", "Brendan Eich", "Anders Hejlsberg"],
             "answer": 0, "difficulty": "medium", "tag": "dev"},
            {"q": "Photoshop was made by which company?",
             "choices": ["Adobe", "Apple", "Autodesk", "Corel"],
             "answer": 0, "difficulty": "easy", "tag": "tools"},
            {"q": "What does 'SaaS' stand for?",
             "choices": ["Software as a Service", "System as a Server", "Software and Services", "Secure as a Service"],
             "answer": 0, "difficulty": "easy", "tag": "basics"},
            {"q": "Python is named after...?",
             "choices": ["The snake", "Monty Python", "A mathematician", "Mt. Python"],
             "answer": 1, "difficulty": "medium", "tag": "dev"},
            {"q": "Excel was made by which company?",
             "choices": ["Apple", "Microsoft", "Google", "IBM"],
             "answer": 1, "difficulty": "easy", "tag": "tools"},
            {"q": "Which of these is a version control system?",
             "choices": ["Docker", "Kubernetes", "Git", "Jenkins"],
             "answer": 2, "difficulty": "easy", "tag": "dev"},
        ],
        "Cyber Trivia": [
            {"q": "What is 'phishing'?",
             "choices": ["A type of malware", "Tricking users into revealing info", "A way to encrypt files", "A new browser"],
             "answer": 1, "difficulty": "easy", "tag": "security"},
            {"q": "Which password is strongest?",
             "choices": ["password123", "qwerty", "correct horse battery staple", "MyDog2014"],
             "answer": 2, "difficulty": "easy", "tag": "security"},
            {"q": "What does 'VPN' stand for?",
             "choices": ["Virtual Private Network", "Verified Personal Net", "Variable Privacy Number", "Verified Public Net"],
             "answer": 0, "difficulty": "easy", "tag": "security"},
            {"q": "A 'zero-day' is...?",
             "choices": ["A vulnerability not yet patched", "A fast internet day", "A 24h sale", "A Linux command"],
             "answer": 0, "difficulty": "medium", "tag": "security"},
            {"q": "Which is a sign of a phishing email?",
             "choices": ["Urgent tone + suspicious link", "Personalized greeting", "Long signature", "Sent from your own address"],
             "answer": 0, "difficulty": "easy", "tag": "security"},
            {"q": "What does '2FA' stand for?",
             "choices": ["Two-Factor Authentication", "Two Form Authentication", "Trusted Factor Access", "Token Form Auth"],
             "answer": 0, "difficulty": "easy", "tag": "security"},
            {"q": "Which encryption is considered strongest today?",
             "choices": ["AES-256", "ROT13", "WEP", "MD5"],
             "answer": 0, "difficulty": "medium", "tag": "security"},
            {"q": "Ransomware does what?",
             "choices": ["Encrypts files until ransom", "Speeds up your computer", "Improves security", "Opens a browser"],
             "answer": 0, "difficulty": "easy", "tag": "security"},
        ],
        "Social Media": [
            {"q": "Twitter was renamed to what in 2023?",
             "choices": ["X", "Twttr", "Birdy", "TweetWorks"],
             "answer": 0, "difficulty": "easy", "tag": "modern"},
            {"q": "Instagram was founded in which year?",
             "choices": ["2008", "2010", "2012", "2014"],
             "answer": 1, "difficulty": "medium", "tag": "modern"},
            {"q": "Which platform is most associated with short video clips?",
             "choices": ["Facebook", "LinkedIn", "TikTok", "Reddit"],
             "answer": 2, "difficulty": "easy", "tag": "modern"},
            {"q": "Snapchat was famous for which feature?",
             "choices": ["Long-form posts", "Disappearing messages", "Job listings", "Live auctions"],
             "answer": 1, "difficulty": "easy", "tag": "modern"},
            {"q": "LinkedIn is mainly used for...?",
             "choices": ["Dating", "Professional networking", "Gaming", "Music sharing"],
             "answer": 1, "difficulty": "easy", "tag": "basics"},
            {"q": "Which company owns Instagram?",
             "choices": ["Google", "Meta (Facebook)", "Twitter/X", "Microsoft"],
             "answer": 1, "difficulty": "easy", "tag": "modern"},
            {"q": "What is the character limit on X (formerly Twitter)?",
             "choices": ["140", "280", "500", "1000"],
             "answer": 1, "difficulty": "medium", "tag": "basics"},
            {"q": "Reddit calls its topic communities...?",
             "choices": ["Channels", "Subreddits", "Boards", "Threads"],
             "answer": 1, "difficulty": "easy", "tag": "basics"},
        ],
    },
    "kids-birthday": {
        "Disney": [
            {"q": "Who is the snowman in 'Frozen'?",
             "choices": ["Olaf", "Sven", "Kristoff", "Hans"],
             "answer": 0, "difficulty": "easy", "tag": "kids"},
            {"q": "What kind of animal is Bambi?",
             "choices": ["Rabbit", "Deer", "Bear", "Fox"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "What does Simba become in 'The Lion King'?",
             "choices": ["Chief", "King", "Hunter", "Wise Man"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Which Disney princess has a magic carpet?",
             "choices": ["Belle", "Ariel", "Jasmine", "Cinderella"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Which Disney movie features the song 'Let It Go'?",
             "choices": ["Moana", "Frozen", "Tangled", "Brave"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "What color is Mickey Mouse's shorts?",
             "choices": ["Blue", "Black", "Red", "Yellow"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Nemo is a fish in which film?",
             "choices": ["Shark Tale", "Finding Nemo", "The Little Mermaid", "Moana"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "What is the name of the princess in 'The Little Mermaid'?",
             "choices": ["Belle", "Moana", "Ariel", "Elsa"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
        ],
        "Animals": [
            {"q": "What do bees make?",
             "choices": ["Milk", "Honey", "Cheese", "Bread"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "How many legs does an octopus have?",
             "choices": ["6", "8", "10", "12"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Which is the fastest land animal?",
             "choices": ["Lion", "Horse", "Cheetah", "Greyhound"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Which animal says 'moo'?",
             "choices": ["Sheep", "Pig", "Cow", "Horse"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "What is a baby kangaroo called?",
             "choices": ["Cub", "Kitten", "Joey", "Pup"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Penguins can fly. True or false?",
             "choices": ["True", "False"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "How many legs does a spider have?",
             "choices": ["6", "8", "10", "12"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "What color are flamingos?",
             "choices": ["Blue", "Pink", "Yellow", "White"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
        ],
        "Cartoon Characters": [
            {"q": "What color is SpongeBob?",
             "choices": ["Pink", "Yellow", "Blue", "Orange"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Who lives in a pineapple under the sea?",
             "choices": ["Patrick", "Squidward", "SpongeBob", "Sandy"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Mickey Mouse's dog is named...",
             "choices": ["Goofy", "Pluto", "Donald", "Max"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Tom is the cat in which cartoon?",
             "choices": ["Looney Tunes", "Tom & Jerry", "Garfield", "Felix"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Bart Simpson's dad is...?",
             "choices": ["Ned", "Moe", "Homer", "Marge"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Snoopy is what kind of animal?",
             "choices": ["Cat", "Dog", "Bear", "Rabbit"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Who is the pet dinosaur of the Rugrats?",
             "choices": ["Spike", "Rex", "Dino", "Rufus"],
             "answer": 1, "difficulty": "medium", "tag": "kids",
             "hint": "All grown up too."},
            {"q": "Garfield hates which day of the week?",
             "choices": ["Sunday", "Monday", "Wednesday", "Friday"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
        ],
        "Roblox & Minecraft": [
            {"q": "Minecraft: what do you mine?",
             "choices": ["Gold", "Diamonds", "Blocks", "Coal"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Roblox is a platform primarily for...",
             "choices": ["Watching movies", "Playing/building games", "Reading books", "Shopping"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Minecraft's main enemy mob is...",
             "choices": ["Zombie", "Creeper", "Spider", "Skeleton"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "In Minecraft, what does TNT stand for?",
             "choices": ["Tricky Nitro Time-bomb", "Trinitrotoluene", "Tiny Nuclear Tester", "Tiny Not-To-Touch"],
             "answer": 1, "difficulty": "medium", "tag": "kids"},
            {"q": "The default player skin in Minecraft is named?",
             "choices": ["Steve", "Alex", "Herobrine", "Jens"],
             "answer": 0, "difficulty": "easy", "tag": "kids"},
            {"q": "Roblox's currency is...?",
             "choices": ["Coins", "Gems", "Robux", "Credits"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "In Roblox, who runs the platform?",
             "choices": ["Roblox Corporation", "Microsoft", "Google", "Epic Games"],
             "answer": 0, "difficulty": "medium", "tag": "kids"},
            {"q": "What material is often used for pickaxes in Minecraft?",
             "choices": ["Stone", "Wood", "Iron", "All of the above"],
             "answer": 3, "difficulty": "easy", "tag": "kids"},
        ],
        "Superheroes": [
            {"q": "Which superhero uses a spider sense?",
             "choices": ["Batman", "Superman", "Spider-Man", "Iron Man"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "What is Captain America's shield made of?",
             "choices": ["Steel", "Titanium", "Vibranium", "Carbon"],
             "answer": 2, "difficulty": "medium", "tag": "kids"},
            {"q": "What color is The Hulk?",
             "choices": ["Red", "Blue", "Green", "Yellow"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Superman's weakness is...?",
             "choices": ["Gold", "Kryptonite", "Silver", "Diamonds"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "What is Batman's real name?",
             "choices": ["Clark Kent", "Bruce Wayne", "Tony Stark", "Peter Parker"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "Thor's hammer is called...?",
             "choices": ["Mjollnir", "Sting", "Excalibur", "Gungnir"],
             "answer": 0, "difficulty": "medium", "tag": "kids"},
            {"q": "Which hero is known as 'The Man of Steel'?",
             "choices": ["Batman", "Iron Man", "Superman", "Aquaman"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "The Avengers' headquarters was once in which NYC building?",
             "choices": ["Empire State", "Stark Tower", "World Trade Center", "Flatiron"],
             "answer": 1, "difficulty": "medium", "tag": "marvel"},
        ],
        "School Stuff": [
            {"q": "How many days are in a typical school week (Mon-Fri)?",
             "choices": ["3", "4", "5", "6"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "What do you use to erase pencil marks?",
             "choices": ["Sharpener", "Eraser", "Glue", "Crayon"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "How many sides does a triangle have?",
             "choices": ["3", "4", "5", "6"],
             "answer": 0, "difficulty": "easy", "tag": "kids"},
            {"q": "What do you call the person who teaches a class?",
             "choices": ["Principal", "Teacher", "Coach", "Janitor"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "How many cents in a dollar?",
             "choices": ["10", "100", "1000", "50"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
            {"q": "What color do you get mixing red and yellow?",
             "choices": ["Green", "Purple", "Orange", "Pink"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "What planet do we live on?",
             "choices": ["Mars", "Venus", "Earth", "Jupiter"],
             "answer": 2, "difficulty": "easy", "tag": "kids"},
            {"q": "Which is the largest ocean?",
             "choices": ["Atlantic", "Pacific", "Indian", "Arctic"],
             "answer": 1, "difficulty": "easy", "tag": "kids"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Risk / overlap / scoring helpers
# ---------------------------------------------------------------------------

HOST_GUIDE_NOTES = {
    "general": "Universal — works for almost any audience.",
    "geography": "Pair with a large map or atlas prop for extra fun.",
    "music": "You'll need a way to play short audio clips (YouTube playlists work).",
    "movie-tv": "Picture round is the centerpiece — print A4-sized images.",
    "sports": "Stack the music + picture rounds lightly; this is a stats crowd.",
    "history": "Tougher material — pair with an easier follow-up round.",
    "science": "Keep questions concrete — avoid pure textbook chemistry.",
    "food-drink": "Crowd-pleaser — lean into 'guess the dish from the photo'.",
    "literature": "Best for literate crowds — sketch the picture round carefully.",
    "1980s": "Nostalgia weapon. Works for 40+.",
    "1990s": "Works for 30+ — second-heaviest nostalgia decade.",
    "office": "Best for work-team-bonding events, not mixed-age crowds.",
    "holiday": "Seasonal hit — pair with themed snacks.",
    "tech": "Tech-adjacent crowd. Avoid self-driving detailed questions.",
    "kids-birthday": "Keep ALL questions easy. Aim ages 8-12.",
}

AMBIGUOUS_PHRASES = [
    "what is the most", "which is best", "popular", "famous", "well-known",
    "main", "major", "favorite", "greatest",
]


def _stable_seed(seed: str) -> int:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _pick_questions(theme: str, rounds: list[str], qpr: int, seed_key: str) -> list[dict]:
    """Return a list of {round, questions: [...]} dicts, deterministic per seed_key."""
    bank = QUESTION_BANK.get(theme, QUESTION_BANK["general"])
    rounds_meta = []
    for i, rnd in enumerate(rounds):
        pool = bank.get(rnd) or bank.get("Picture Round") or list(bank.values())[0]
        # Always substitute with a real sub-bank if available; otherwise a generic one
        # for the theme. We fall back to general round of same slug name if missing.
        if not pool:
            pool = QUESTION_BANK["general"].get(rnd) or QUESTION_BANK["general"]["Picture Round"]
        # We need at least qpr; gather extras for variety
        pool = list(pool)
        # deterministic shuffle
        s = _stable_seed(f"{seed_key}|r{i}|{rnd}")
        pool.sort(key=lambda q, sd=s: (_stable_seed(f"{sd}|{q['q']}"), q['q']))
        chosen = pool[:qpr]
        rounds_meta.append({"round": rnd, "questions": chosen})
    return rounds_meta


def _build_host_script(rounds_meta: list[dict], minutes_per_round: int, theme_label: str) -> list[dict]:
    out = []
    for i, rm in enumerate(rounds_meta, start=1):
        n_q = len(rm["questions"])
        out.append({
            "round_no": i,
            "round_name": rm["round"],
            "questions": n_q,
            "estimated_min": minutes_per_round,
            "host_cue": (
                "Read questions aloud one at a time, give teams 30–60s "
                "between questions. For picture rounds: hand out sheets at the "
                "start of the round, collect at the end."
            ),
            "score_after_round": "1 point per correct answer. 0.5 bonus if "
                                 "all teams in agreement on tiebreaker math.",
        })
    out.append({
        "round_no": len(rounds_meta) + 1,
        "round_name": "Tiebreaker (if needed)",
        "questions": 1,
        "estimated_min": 2,
        "host_cue": "Only if two (or more) teams are tied. Closest answer wins.",
        "score_after_round": "No point — purely tiebreaker.",
    })
    return out


def _score_session(
    brief: str, theme: str, rounds_meta: list[dict], n_rounds: int, qpr: int,
    duration_min: int, n_teams: int, vibe: str,
) -> dict:
    """5-axis 0-100 night-feasibility score."""
    # Axis 1 — BALANCE: do rounds vary in difficulty?
    diffs = [q["difficulty"] for rm in rounds_meta for q in rm["questions"]]
    if diffs:
        e = diffs.count("easy")
        m = diffs.count("medium")
        h = diffs.count("hard")
        n = len(diffs)
        ease_ratio = e / n
        # ideal: 40-55% easy, 30-40% medium, 5-15% hard
        balance_pen = 0
        if ease_ratio < 0.30:
            balance_pen += 18
        elif ease_ratio > 0.65:
            balance_pen += 8
        h_ratio = h / n
        if h_ratio > 0.25:
            balance_pen += 10
        balance = 95 - balance_pen
    else:
        balance = 40

    # Axis 2 — PACE: does the duration estimate match round count?
    # rough: 8 Qs @ ~40s each + 1min transition ≈ 6 min/round, plus picture/music rounds run longer
    music_or_picture = sum(1 for rm in rounds_meta if "Picture" in rm["round"] or "Music" in rm["round"])
    per_round_min = qpr * 0.7 + 1.0  # 40s/Q + 1 min transition
    estimated_min = round(per_round_min * n_rounds + music_or_picture * 1.5 + 2)
    pace_gap = abs(estimated_min - duration_min)
    pace = max(40, 100 - pace_gap * 2)

    # Axis 3 — AMBIGUITY: how many ambiguous questions did we end up with?
    flagged = 0
    for rm in rounds_meta:
        for q in rm["questions"]:
            qtext = q["q"].lower()
            for phrase in AMBIGUOUS_PHRASES:
                if phrase in qtext:
                    flagged += 1
                    break
    ambiguity = max(40, 100 - flagged * 6)

    # Axis 4 — ACCESSIBILITY: depends on theme + vibe
    accessibility = 90
    if theme in ("literature", "tech") and vibe in ("family", "kids"):
        accessibility -= 18
    if theme in ("science", "history") and vibe == "kids":
        accessibility -= 22
    if theme in ("literature", "tech", "science", "history") and vibe == "family":
        accessibility -= 8
    if theme in ("music", "movie-tv", "general", "1990s", "1980s") and vibe == "kids":
        accessibility -= 5
    if theme == "kids-birthday" and vibe != "kids":
        accessibility -= 12

    # Axis 5 — REPLAYABILITY: bigger banks get higher scores
    # heuristic: themes with more bank entries get a small bonus
    bank_size = sum(
        len(pool) for pool in QUESTION_BANK.get(theme, {}).values()
    )
    replayability = 70 + min(25, bank_size)

    overall_raw = (
        balance * 0.25 + pace * 0.25 + ambiguity * 0.20
        + accessibility * 0.15 + replayability * 0.15
    )
    overall = round(overall_raw, 1)

    # verdict bands
    if overall >= 85:
        verdict = "ring_the_bell"
    elif overall >= 70:
        verdict = "mostly_ready"
    elif overall >= 55:
        verdict = "needs_polish"
    elif overall >= 40:
        verdict = "tight_but_usable"
    else:
        verdict = "needs_rework"

    return {
        "axes": {
            "balance": balance,
            "pace": pace,
            "ambiguity": ambiguity,
            "accessibility": accessibility,
            "replayability": replayability,
        },
        "overall": overall,
        "verdict": verdict,
        "estimated_min": estimated_min,
    }


def _risk_flags(brief: str, theme: str, rounds_meta: list[dict], n_rounds: int,
                duration_min: int, n_teams: int, vibe: str) -> list[dict]:
    flags = []
    # Overlap risk: rounds sharing the same tag cluster
    tags = []
    for rm in rounds_meta:
        for q in rm["questions"]:
            tags.append(q.get("tag", ""))
    common = max([tags.count(t) for t in set(tags)], default=0)
    if common >= 4:
        flags.append({
            "id": "tag_overlap",
            "severity": "low",
            "category": "content",
            "evidence": f"Tag '{[t for t in set(tags) if tags.count(t) == common][0]}' appears {common}x across the night.",
            "fix": "Consider swapping one round to a different sub-theme for variety.",
        })

    # Runaway leader risk: too many easy questions favor the strongest team
    n_total = sum(len(rm["questions"]) for rm in rounds_meta)
    n_easy = sum(1 for rm in rounds_meta for q in rm["questions"] if q["difficulty"] == "easy")
    if n_total and n_easy / n_total > 0.7:
        flags.append({
            "id": "runaway_leader",
            "severity": "low",
            "category": "balance",
            "evidence": f"{n_easy}/{n_total} questions are easy — quick recall favors the speediest team.",
            "fix": "Use the 50/50 mechanic on one round and a double-points bonus round to widen the field.",
        })

    # Too short: rounds < 5 means quick night
    if n_rounds < 5:
        flags.append({
            "id": "too_short",
            "severity": "low",
            "category": "structure",
            "evidence": f"Only {n_rounds} rounds — under 45 minutes total.",
            "fix": "Add a music round or picture round to extend without adding prep time.",
        })

    # Duration mismatch
    if duration_min > 0 and duration_min < 30:
        flags.append({
            "id": "too_short_duration",
            "severity": "medium",
            "category": "structure",
            "evidence": f"Duration goal of {duration_min} min is unusually tight.",
            "fix": "Either increase duration or accept that some rounds get dropped on the fly.",
        })
    if duration_min > 180:
        flags.append({
            "id": "too_long",
            "severity": "medium",
            "category": "structure",
            "evidence": f"Duration goal of {duration_min} min exceeds 3 hours.",
            "fix": "Add a 15-minute intermission and provide snacks at the half — pace will save itself.",
        })

    # Vibe mismatch
    if theme == "kids-birthday" and vibe not in ("kids", "mixed"):
        flags.append({
            "id": "vibe_mismatch_kids",
            "severity": "medium",
            "category": "vibe",
            "evidence": "Theme 'Kids' Birthday' but crowd vibe is adult.",
            "fix": "Pick a different theme or explicitly ask adults to pair up with kids as teams.",
        })

    if theme in ("literature", "history", "science") and vibe == "kids":
        flags.append({
            "id": "vibe_mismatch_kids_hard",
            "severity": "high",
            "category": "vibe",
            "evidence": f"Theme '{theme}' is academic, vibe is kids.",
            "fix": "Pre-screen: skip any question kids likely can't answer, or swap theme entirely.",
        })

    # Picture round without image capability
    has_picture = any("Picture" in rm["round"] for rm in rounds_meta)
    if has_picture:
        flags.append({
            "id": "picture_round_logistics",
            "severity": "low",
            "category": "logistics",
            "evidence": "Picture rounds require printed A4 sheets.",
            "fix": "Prep the picture sheets 24h in advance; consider PowerPoint if no printer.",
        })

    has_music = any("Music" in rm["round"] for rm in rounds_meta)
    if has_music:
        flags.append({
            "id": "music_round_logistics",
            "severity": "low",
            "category": "logistics",
            "evidence": "Music rounds need a clean audio + speaker setup.",
            "fix": "Pre-build a YouTube / Spotify playlist with timestamps and test the speakers.",
        })

    # Teams > 0 but unreasonable
    if n_teams > 12:
        flags.append({
            "id": "too_many_teams",
            "severity": "low",
            "category": "logistics",
            "evidence": f"{n_teams} teams will slow scoring significantly.",
            "fix": "Cap at 8 teams, or use a Google Form for answer submission.",
        })

    # Brief mentions nothing — possible empty brief
    if len(brief.strip()) < 8:
        flags.append({
            "id": "empty_brief",
            "severity": "medium",
            "category": "input",
            "evidence": "Brief is very short — we're generating blind defaults.",
            "fix": "Add at least the vibe and one off-limit topic so the night feels intentional.",
        })

    return flags


def _tiebreaker(session_id: str, idx: int = 0) -> dict:
    """Deterministic per session tiebreaker math problem."""
    seed = _stable_seed(session_id)
    a = (seed % 30) + 12  # 12..41
    b = ((seed >> 4) % 11) + 3  # 3..13
    c = ((seed >> 8) % 7) + 2  # 2..8
    d = ((seed >> 12) % 19) + 5  # 5..23
    if idx == 0:
        # (a + b) * c - d
        answer = (a + b) * c - d
        return {
            "problem": f"Compute ({a} + {b}) × {c} - {d}",
            "answer": answer,
        }
    # alternate: (a × b) + (c × d) / 2
    if idx == 1:
        answer = (a * b) + (c * d) // 2
        return {
            "problem": f"Compute ({a} × {b}) + ({c} × {d}) ÷ 2 (round down)",
            "answer": answer,
        }
    answer = (a + b + c) * d
    return {"problem": f"Compute ({a} + {b} + {c}) × {d}", "answer": answer}


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            brief TEXT,
            theme TEXT,
            n_rounds INTEGER,
            qpr INTEGER,
            duration_min INTEGER,
            n_teams INTEGER,
            vibe TEXT,
            payload_json TEXT,
            self_rating INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
        """)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    brief = (data.get("brief") or "").strip()
    theme = (data.get("theme") or "general").strip()
    n_rounds = int(data.get("n_rounds") or 8)
    qpr = int(data.get("qpr") or 8)
    duration_min = int(data.get("duration_min") or 90)
    n_teams = int(data.get("n_teams") or 6)
    vibe = (data.get("vibe") or "mixed").strip().lower()

    if theme not in THEMES:
        theme = "general"
    n_rounds = max(3, min(12, n_rounds))
    qpr = max(4, min(10, qpr))
    duration_min = max(20, min(240, duration_min))
    n_teams = max(2, min(20, n_teams))
    if vibe not in ("family", "kids", "adult", "mixed", "work"):
        vibe = "mixed"

    theme_meta = THEMES[theme]
    rounds = theme_meta["rounds"][:n_rounds]
    if len(rounds) < n_rounds:
        # pad from general pool if theme has fewer than n_rounds
        extra = THEMES["general"]["rounds"]
        i = 0
        while len(rounds) < n_rounds:
            cand = extra[i % len(extra)]
            if cand not in rounds:
                rounds.append(cand)
            i += 1

    sid = uuid.uuid4().hex[:12]
    rounds_meta = _pick_questions(theme, rounds, qpr, sid)
    host_script = _build_host_script(rounds_meta, minutes_per_round=8, theme_label=theme_meta["label"])
    score = _score_session(brief, theme, rounds_meta, n_rounds, qpr, duration_min, n_teams, vibe)
    flags = _risk_flags(brief, theme, rounds_meta, n_rounds, duration_min, n_teams, vibe)
    tiebreaker = _tiebreaker(sid)

    # Suggested team balance handicaps (if N teams and one strong/one weak vibe)
    handicap = []
    if n_teams >= 2:
        handicap = [
            "Stronger teams: pick last on team-color.",
            "Weaker teams: +0.5 bonus point on tiebreaker math.",
            "Picture round: more time (90s vs 60s).",
        ]

    payload = {
        "session_id": sid,
        "brief": brief,
        "theme": theme,
        "theme_label": theme_meta["label"],
        "theme_description": theme_meta["description"],
        "host_guide_note": HOST_GUIDE_NOTES.get(theme, ""),
        "n_rounds": n_rounds,
        "qpr": qpr,
        "duration_min": duration_min,
        "n_teams": n_teams,
        "vibe": vibe,
        "rounds": rounds_meta,
        "host_script": host_script,
        "tiebreaker": tiebreaker,
        "handicap_notes": handicap,
        "score": score,
        "risk_flags": flags,
        "created_at": int(time.time()),
    }

    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at, brief, theme, n_rounds, qpr, "
            "duration_min, n_teams, vibe, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, int(time.time()), brief, theme, n_rounds, qpr, duration_min,
             n_teams, vibe, json.dumps(payload)),
        )

    return jsonify(payload)


@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, created_at, theme, n_rounds, qpr, n_teams, vibe, self_rating, notes "
            "FROM sessions ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "theme": r["theme"],
            "n_rounds": r["n_rounds"],
            "qpr": r["qpr"],
            "n_teams": r["n_teams"],
            "vibe": r["vibe"],
            "self_rating": r["self_rating"],
            "notes": r["notes"],
        })
    return jsonify({"sessions": out})


@app.route("/api/sessions/<sid>", methods=["GET"])
def get_session(sid):
    with db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"error": "not_found"}), 404
    payload = json.loads(row["payload_json"])
    payload["self_rating"] = row["self_rating"]
    payload["notes"] = row["notes"]
    return jsonify(payload)


@app.route("/api/sessions/<sid>", methods=["PATCH"])
def patch_session(sid):
    data = request.get_json(force=True)
    self_rating = data.get("self_rating")
    notes = data.get("notes")
    with db() as conn:
        if self_rating is not None:
            conn.execute("UPDATE sessions SET self_rating = ? WHERE id = ?",
                         (int(self_rating), sid))
        if notes is not None:
            conn.execute("UPDATE sessions SET notes = ? WHERE id = ?",
                         (notes, sid))
    return jsonify({"ok": True})


@app.route("/api/export/<sid>", methods=["GET"])
def export_session(sid):
    fmt = request.args.get("fmt", "md")
    with db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"error": "not_found"}), 404
    payload = json.loads(row["payload_json"])
    if fmt == "json":
        return jsonify(payload)
    # Markdown
    md = _render_markdown(payload, row["self_rating"], row["notes"])
    return md, 200, {"Content-Type": "text/markdown; charset=utf-8",
                     "Content-Disposition": f"attachment; filename=pub-trivia-{sid}.md"}


def _render_markdown(p: dict, self_rating: int, notes: str) -> str:
    sc = p["score"]
    lines = []
    lines.append(f"# Pub-Quiz Night — {p['theme_label']}")
    lines.append("")
    lines.append(f"_Session: `{p['session_id']}` · {p['n_rounds']} rounds × {p['qpr']} Qs · ~{p['duration_min']} min · {p['n_teams']} teams · vibe: {p['vibe']}_")
    lines.append("")
    lines.append(f"**Score:** {sc['overall']} / 100 — `{sc['verdict']}`")
    lines.append("")
    lines.append("**5-axis breakdown:**")
    for k, v in sc["axes"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    if p.get("host_guide_note"):
        lines.append(f"> {p['host_guide_note']}")
        lines.append("")
    if p.get("risk_flags"):
        lines.append("## Risk flags")
        for f in p["risk_flags"]:
            lines.append(f"- **[{f['severity'].upper()}]** {f['id']}: {f['evidence']}")
            lines.append(f"  - _Fix:_ {f['fix']}")
        lines.append("")
    lines.append("## Host script")
    for hs in p["host_script"]:
        lines.append(f"### Round {hs['round_no']}: {hs['round_name']}")
        lines.append(f"- {hs['questions']} questions · est. {hs['estimated_min']} min")
        lines.append(f"- _Host cue:_ {hs['host_cue']}")
        lines.append(f"- _Scoring:_ {hs['score_after_round']}")
        lines.append("")
    lines.append("## Questions")
    for rm in p["rounds"]:
        lines.append(f"### {rm['round']}")
        for i, q in enumerate(rm["questions"], start=1):
            lines.append(f"**Q{i}.** {q['q']}")
            for j, c in enumerate(q["choices"]):
                letter = "ABCD"[j]
                mark = " **(✓)**" if j == q["answer"] else ""
                lines.append(f"  - {letter}. {c}{mark}")
            if q.get("hint"):
                lines.append(f"  - _Hint:_ {q['hint']}")
        lines.append("")
    lines.append("## Tiebreaker")
    tb = p["tiebreaker"]
    lines.append(f"- {tb['problem']}  ")
    lines.append(f"- _Answer:_ {tb['answer']}")
    if p.get("handicap_notes"):
        lines.append("")
        lines.append("## Team balance")
        for h in p["handicap_notes"]:
            lines.append(f"- {h}")
    if self_rating or notes:
        lines.append("")
        lines.append("## Reflection")
        if self_rating:
            lines.append(f"- **Self-rating:** {self_rating}/5")
        if notes:
            lines.append(f"- **Notes:** {notes}")
    return "\n".join(lines)


@app.route("/api/themes", methods=["GET"])
def themes():
    return jsonify({"themes": [
        {"id": k, "label": v["label"], "description": v["description"],
         "tags": v["tags"], "n_rounds_available": len(v["rounds"])}
        for k, v in THEMES.items()
    ]})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "ts": int(time.time())})


@app.route("/api/sample", methods=["GET"])
def sample():
    """Return a sample brief so the UI can prefill on first run."""
    return jsonify({
        "brief": (
            "Saturday night, hosting 8 friends at home for our annual game night. "
            "Mixed group — 3 of us are engineers, 2 teachers, 2 retail managers, "
            "1 grad student. Last year everyone was hyped about music and history, "
            "so lean there. 90 minutes including a 10-minute break."
        ),
        "theme": "music",
        "n_rounds": 8,
        "qpr": 8,
        "duration_min": 90,
        "n_teams": 6,
        "vibe": "mixed",
    })


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5071, debug=False)
