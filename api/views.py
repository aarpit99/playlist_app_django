import ast
import os
import random
import re
import string
from functools import reduce
from math import gcd

import simplejson as json
from celery.result import AsyncResult
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from pytube import Playlist as YTPlaylist

from playlist import settings
from .models import Playlist, Game
from .tasks import download_and_save_music_locally, send_code_via_email


def index(request):
    return render(request, 'index.html')


def terms_and_conditions(request):
    return render(request, 'tnc.html')


def game_info_howto(request):
    return render(request, 'gameinfo.html')


def about_us(request):
    return render(request, 'aboutus.html')


def feedback(request):
    return render(request, 'feedback.html')


def thanks(request):
    return render(request, 'thanks.html')


def new_game(request):
    return render(request, 'create_game.html')


def new_entry(request):
    return render(request, 'playlist.html')


def play_games(request):
    return render(request, 'games.html')


@csrf_exempt
def create_game(request):
    try:
        name = request.POST.get('name')
        sample_size = int(request.POST.get('sample_size'))
        pool_size = int(request.POST.get('pool_size'))
        contestants = int(request.POST.get('contestants'))
        email = request.POST.get('email')

        regex = r'^[a-z0-9]+[\._]?[a-z0-9]+[@]\w+[.]\w{2,3}$'
        if not re.search(regex, email):
            return JsonResponse({'status': 'FAILED', 'message': 'Please enter a valid email ID'})

        game_list = [game.name for game in Game.objects.all()]

        error = "Sorry, A game with the same name already exists ;)"
        if name in game_list:
            return JsonResponse({'status': 'FAILED', 'message': error})

        error = f"No of songs to be played should be between {contestants} and {pool_size * contestants}"
        if not contestants <= sample_size <= pool_size * contestants:
            return JsonResponse({'status': 'FAILED', 'message': error})

        data = {
            'name': name,
            'sample_size': sample_size,
            'pool_size': pool_size,
            'contestants': contestants,
            'game_code': ''.join(random.choices(string.ascii_letters + string.digits, k=6))

        }
        game = Game(**data)
        game.save()
        response = {
            'status': 'SUCCESS',
            'message': 'Game Created',
            'game_code': data['game_code']
        }

        subject = 'Unique code for the game you just created'
        body = f"""<!DOCTYPE html><html><body>Hi,<br><br>This mail contains the secret code for the game you just created.
        <br>Please don't share it with anyone.<br>You have to use the code to start the game.<br>
        Your unique code is :<div style='font-weight: bolder; font-style: italic; color: darkcyan'>
        {data['game_code']}</div><br><br><br>Have a great game!!!<br><br>Regards,<br>
        The Playlist Game Team</body></html>"""

        support = settings.EMAIL_HOST_USER
        send_code_via_email.delay(subject, body, support, email)

    except Exception as e:
        response = {
            'status': 'FAILED',
            'message': str(e)
        }
    return JsonResponse(response)


@csrf_exempt
def put_playlist(request):
    try:
        name = request.POST.get('name')
        game = request.POST.get('game')
        playlist = request.POST.get('playlist')
        game_obj = Game.objects.get(id=game)
        playlist_object = Playlist.objects.filter(game=game)

        error = "Sorry, I guess you have a very common name ;)"
        username_list = [player_list.name for player_list in playlist_object]
        if name in username_list:
            return JsonResponse({'status': 'FAILED', 'message': error})

        playlist_dict, error_data, jobs, duplicate_songs = {}, {}, [], []
        try:
            pl = YTPlaylist(playlist)
            if isinstance(pl, YTPlaylist) and not pl:
                return JsonResponse({'status': 'FAILED', 'message': 'Empty or private Playlist URL'})
        except (AttributeError, Exception):
            return JsonResponse({'status': 'FAILED', 'message': 'Invalid Playlist URL'})

        song_list = json.loads(game_obj.all_songs)
        for song in pl:
            if song in song_list:
                duplicate_songs.append(song)
        if duplicate_songs:
            error_data['duplicate'] = duplicate_songs

        game_pool_size = game_obj.pool_size
        playlist_length = len(pl)
        length_error = f"Sorry, Your playlist should be of length {game_pool_size}! ;)"
        if not playlist_length == game_pool_size:
            error_data['length'] = length_error
        if error_data:
            error_data['status'] = 'FAILED'
            return JsonResponse(error_data)

        for idx, link in enumerate(pl):
            filename = f"{name.lower().replace(' ', '_')}_{game.replace(' ', '_')}_{idx + 1}"
            result = download_and_save_music_locally.delay(filename, link)
            jobs.append(result.task_id)
            all_songs = json.loads(game_obj.all_songs)
            all_songs.append(link)
            game_obj.all_songs = json.dumps(all_songs)
            game_obj.save()
            playlist_dict[idx + 1] = os.path.join(f'{filename}.mp3')
        data = {
            'name': name.lower(),
            'game': game_obj,
            'playlist': json.dumps(playlist_dict)
        }
        p = Playlist(**data)
        p.save()
        playlist_object = Playlist.objects.filter(game=game)
        if len(playlist_object) == game_obj.contestants:
            game_obj.ready_to_play = 1
            game_obj.save()
        return JsonResponse({'status': 'SUCCESS', 'message': 'Playlist Submitted', 'jobs': jobs})
    except Exception as e:
        return JsonResponse({'status': 'FAILED', 'message': str(e)})


@csrf_exempt
def poll_download(request):
    try:
        jobs = ast.literal_eval(request.POST.get('jobs'))
        result = []
        for task in jobs:
            res = AsyncResult(task)
            result.append({'state': res.status, 'info': res.result})
        return JsonResponse({'result': result})
    except Exception as e:
        return JsonResponse({'message': str(e)})


@csrf_exempt
def get_games(request):
    try:
        game = int(request.POST.get('game'))
        game_code = Game.objects.get(id=game).game_code
        if game_code == request.POST.get('game_code'):
            return JsonResponse({'status': 'success'})
        else:
            return JsonResponse({'message': 'Game code doesn\'t match'})
    except Exception as e:
        return JsonResponse({'message': str(e)})


def randomise(request, game):
    try:
        all_playlist = {}
        all_random_sample = []
        uniquePlayers = []
        playlist = Playlist.objects.filter(game=game)
        game_object = Game.objects.get(id=game)
        sample_size = game_object.sample_size
        scorecard = dict()
        for player in playlist:
            scorecard[player.name] = 0
        game_object.score = json.dumps(scorecard)
        game_object.save()
        for obj in playlist:
            all_playlist[obj.name] = json.loads(obj.playlist)
            uniquePlayers.append(obj.name)
        for idx, i in all_playlist.items():
            all_random_sample.extend({'name': idx, 'link': songs} for songs in list(i.values()))
        random.shuffle(all_random_sample)
        all_random_sample = random.sample(all_random_sample, k=sample_size)
        return render(request, 'songs.html',
                      {'context': all_random_sample, 'uniquePlayers': uniquePlayers, 'scorecard': scorecard})
    except Exception as e:
        return JsonResponse({'message': str(e)})


def lcm(denominators):
    return reduce(lambda a, b: a * b // gcd(a, b), denominators)


@csrf_exempt
def vote(request):
    try:
        game = request.POST.get('game')
        game_obj = Game.objects.get(id=game)
        scorecard = json.loads(game_obj.score)
        votes = json.loads(request.POST.get('votes'))
        answer = request.POST.get('answer')
        max_score = lcm([i + 1 for i in range(len(scorecard))])
        results = {player: 1 for player in scorecard if player in votes and votes[player] == answer}
        correct_answers = len(results)
        if correct_answers:
            round_score = max_score / correct_answers
            scorecard.update({player: scorecard[player] + round_score for player in results})
        else:
            round_score = max_score
            scorecard.update({player: scorecard[player] + round_score for player in scorecard if player == answer})
        game_obj.score = json.dumps(scorecard)
        game_obj.save()
        return JsonResponse(scorecard)
    except Exception as e:
        return JsonResponse({'message': str(e)})


@csrf_exempt
def game_info(request):
    try:
        game = request.POST.get('game')
        game_obj = Game.objects.get(id=game)
        playlist_obj = Playlist.objects.filter(game=game_obj)
        response = {
            'contestants': [player.name for player in playlist_obj],
            'available_slots': game_obj.contestants - len(playlist_obj),
            'playlist_length': game_obj.pool_size
        }
        return JsonResponse(response)
    except Exception as e:
        return JsonResponse({'message': str(e)})


@csrf_exempt
def game_data(request):
    ready_to_play = int(request.POST.get('ready_to_play'))
    if ready_to_play == 0:
        game_obj = Game.objects.filter(ready_to_play=0)
    else:
        game_obj = Game.objects.filter(ready_to_play=1, is_over=0)

    return JsonResponse([{'key': game.id, 'name': game.name} for game in game_obj], safe=False)


@csrf_exempt
def send_support_mail(request):
    subject = request.POST.get('subject')
    body = request.POST.get('body')
    email = request.POST.get('email')
    support = settings.EMAIL_HOST_USER
    attachments = request.FILES.getlist('attachment')
    try:
        mail = EmailMessage(subject, body, email, [support])
        for attachment in attachments:
            mail.attach(attachment.name, attachment.read(), attachment.content_type)
        mail.send()
        return JsonResponse({'message': 'success'})
    except Exception as e:
        return JsonResponse({'message': str(e)})


@csrf_exempt
def end_game(request):
    try:
        list_of_winners = []
        game_obj = Game.objects.get(id=request.POST.get('game'))
        score = json.loads(game_obj.score)
        winner = max(score, key=score.get)
        for key, value in score.items():
            if value == score[winner]:
                list_of_winners.append(key)
        game_obj.is_over = 1
        game_obj.save()
        playlist_obj = Playlist.objects.filter(game=game_obj)
        song_list = []
        for row in playlist_obj:
            song_list.extend(json.loads(row.playlist).values())
        delete_message = 'Already empty!'
        workdir = 'api/static/music'
        static_dir = 'staticfiles'
        for file in os.listdir(workdir):
            if file in song_list:
                try:
                    os.remove(f'{workdir}/{file}')
                    try:
                        os.remove(f'{static_dir}/{file}')
                    except FileNotFoundError:
                        pass
                    delete_message = 'SUCCESS'
                except Exception as e:
                    delete_message = str(e)
        return JsonResponse({'message': list_of_winners, 'delete_message': delete_message})
    except Exception as e:
        return JsonResponse({'message': str(e)})
