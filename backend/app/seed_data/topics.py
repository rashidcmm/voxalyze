"""Seed data for the topic bank (Phase 1).

Each entry: (text, category, difficulty)
category in {current-affairs, tech, ethics, abstract, india-specific}
difficulty in {1, 2, 3}  (1 = easiest / most familiar, 3 = hardest / most abstract)
"""

TOPICS: list[tuple[str, str, int]] = [
    # current-affairs
    ("Should social media platforms be held legally responsible for misinformation spread on them?", "current-affairs", 2),
    ("Is remote work here to stay, or will companies eventually force a full return to office?", "current-affairs", 1),
    ("Should there be a global carbon tax to fight climate change?", "current-affairs", 2),
    ("Is cancel culture a legitimate form of accountability or a threat to free speech?", "current-affairs", 2),
    ("Should countries open their borders to more climate refugees?", "current-affairs", 3),
    ("Is the gig economy good for workers in the long run?", "current-affairs", 2),
    ("Should influencers be regulated the same way traditional advertisers are?", "current-affairs", 1),
    ("Is nuclear energy the answer to the world's energy crisis?", "current-affairs", 2),
    ("Should billionaires be allowed to exist?", "current-affairs", 2),
    ("Is a four-day work week realistic for most industries?", "current-affairs", 1),
    ("Should news organizations be banned from using anonymous sources?", "current-affairs", 2),
    ("Is globalization slowing down, and is that a good thing?", "current-affairs", 3),

    # tech
    ("Will AI create more jobs than it destroys?", "tech", 1),
    ("Should facial recognition technology be banned in public spaces?", "tech", 2),
    ("Is it ethical for companies to train AI models on publicly available data without consent?", "tech", 2),
    ("Should social media have a minimum age limit stricter than 13?", "tech", 1),
    ("Is open-source AI safer than closed, proprietary AI?", "tech", 3),
    ("Should self-driving cars be allowed to make life-or-death decisions?", "tech", 2),
    ("Is the metaverse a genuine technological shift or a marketing fad?", "tech", 1),
    ("Should coding be a mandatory subject in schools?", "tech", 1),
    ("Is quantum computing going to make current encryption obsolete within our lifetime?", "tech", 3),
    ("Should tech companies be broken up to prevent monopolies?", "tech", 2),
    ("Is screen time actually harmful, or is that outdated moral panic?", "tech", 1),
    ("Should AI-generated art be eligible for copyright protection?", "tech", 2),

    # ethics
    ("Is it ever ethical to lie to protect someone's feelings?", "ethics", 1),
    ("Should animal testing be banned entirely, even if it slows medical research?", "ethics", 2),
    ("Is it wrong to eat meat given current factory farming practices?", "ethics", 2),
    ("Should euthanasia be legalized everywhere?", "ethics", 3),
    ("Is it ethical to have children given the climate crisis?", "ethics", 3),
    ("Should parents be allowed to choose their child's genetic traits?", "ethics", 3),
    ("Is positive discrimination (affirmative action) fair?", "ethics", 2),
    ("Should we prioritize equality of outcome or equality of opportunity?", "ethics", 3),
    ("Is it ethical for wealthy nations to poach skilled workers from poorer ones?", "ethics", 2),
    ("Should whistleblowers always be protected, regardless of the harm their disclosure causes?", "ethics", 2),
    ("Is it moral to profit from other people's addictions (e.g. gambling, sugar, social media)?", "ethics", 2),

    # abstract
    ("Is it better to be feared or loved as a leader?", "abstract", 1),
    ("Does absolute power always corrupt absolutely?", "abstract", 2),
    ("Is competition or cooperation the bigger driver of human progress?", "abstract", 2),
    ("Can a society be truly free without some inequality?", "abstract", 3),
    ("Is it more important to be right or to be kind?", "abstract", 1),
    ("Does technology bring people closer together or push them further apart?", "abstract", 1),
    ("Is failure a necessary ingredient for success?", "abstract", 1),
    ("Should tradition ever outweigh progress?", "abstract", 2),
    ("Is privacy a right or a privilege in the digital age?", "abstract", 2),
    ("Can true objectivity ever exist in journalism?", "abstract", 3),
    ("Is ambition, on balance, a virtue or a vice?", "abstract", 2),

    # india-specific
    ("Should Hindi be promoted as a common link language across India?", "india-specific", 2),
    ("Is the reservation system in India still serving its original purpose?", "india-specific", 3),
    ("Should India invest more in space exploration given its development challenges?", "india-specific", 1),
    ("Is the Indian education system too exam-focused?", "india-specific", 1),
    ("Should India adopt a uniform civil code?", "india-specific", 3),
    ("Is India's startup boom creating real, lasting economic value?", "india-specific", 2),
    ("Should electoral bonds and political funding in India be fully transparent?", "india-specific", 2),
    ("Is the joint family system still relevant in urban India?", "india-specific", 1),
    ('Should India prioritize manufacturing ("Make in India") over its services economy?', "india-specific", 2),
    ("Is India's public transport infrastructure keeping pace with urbanization?", "india-specific", 1),
    ("Should agricultural subsidies in India be phased out?", "india-specific", 3),
    ("Is India ready for a fully digital rupee (CBDC) to replace physical cash?", "india-specific", 2),
]
